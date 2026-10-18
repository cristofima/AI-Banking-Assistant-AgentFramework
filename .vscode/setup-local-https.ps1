$ErrorActionPreference = 'Stop'
$directory = Join-Path $env:USERPROFILE '.banking-oauth-local'
$certificatePath = Join-Path $directory 'localhost-cert.pem'
$keyPath = Join-Path $directory 'localhost-key.pem'

function Update-LocalCaBundle {
    @'
import os
import ssl
from pathlib import Path

import certifi

folder = Path(os.environ["USERPROFILE"]) / ".banking-oauth-local"
public_roots = Path(certifi.where()).read_bytes()
localhost_cert = (folder / "localhost-cert.pem").read_bytes()
bundle = public_roots.rstrip() + b"\n" + localhost_cert + b"\n"
ssl.create_default_context(cadata=bundle.decode("ascii"))
(folder / "localhost-ca-bundle.pem").write_bytes(bundle)
'@ | rtk proxy uv run --directory app\business-api\identity python -
    if ($LASTEXITCODE -ne 0) { throw 'Local TLS CA bundle preparation failed.' }
    Write-Host 'Python CA bundle prepared with public roots and localhost certificate.'
}

if ((Test-Path $certificatePath) -or (Test-Path $keyPath)) {
    if (-not ((Test-Path $certificatePath) -and (Test-Path $keyPath))) {
        throw 'Incomplete local TLS certificate pair; repair explicitly before retrying.'
    }
    Update-LocalCaBundle
    Write-Host 'Existing local TLS pair preserved.'
    exit 0
}
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$acl = Get-Acl $directory
$acl.SetAccessRuleProtection($true, $false)
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    [System.Security.Principal.WindowsIdentity]::GetCurrent().User,
    'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
)
$acl.AddAccessRule($rule)
Set-Acl -Path $directory -AclObject $acl
$certificate = New-SelfSignedCertificate -DnsName 'localhost' -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 3072 -KeyExportPolicy Exportable -NotAfter (Get-Date).AddMonths(6) -FriendlyName 'Banking local development TLS'
$pfxPath = Join-Path $directory 'localhost.pfx'
$cerPath = Join-Path $directory 'localhost.cer'
$password = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
try {
    Export-PfxCertificate -Cert $certificate -FilePath $pfxPath -Password (ConvertTo-SecureString $password -AsPlainText -Force) | Out-Null
    $env:BANKING_TLS_EXPORT_PASSWORD = $password
    $env:BANKING_TLS_EXPORT_DIRECTORY = $directory
    @'
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

folder = Path(os.environ["BANKING_TLS_EXPORT_DIRECTORY"])
key, cert, _ = load_key_and_certificates(
    (folder / "localhost.pfx").read_bytes(),
    os.environ["BANKING_TLS_EXPORT_PASSWORD"].encode(),
)
if key is None or cert is None:
    raise SystemExit("TLS certificate export failed")
with (folder / "localhost-key.pem").open("xb") as output:
    output.write(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
with (folder / "localhost-cert.pem").open("xb") as output:
    output.write(cert.public_bytes(serialization.Encoding.PEM))
'@ | rtk proxy uv run --directory app\business-api\identity python -
    if ($LASTEXITCODE -ne 0) { throw 'TLS PEM conversion failed.' }
    Export-Certificate -Cert $certificate -FilePath $cerPath | Out-Null
    Import-Certificate -FilePath $cerPath -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null
    Update-LocalCaBundle
    Write-Host 'Localhost TLS certificate generated and trusted for the current Windows user.'
} finally {
    Remove-Item Env:\BANKING_TLS_EXPORT_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:\BANKING_TLS_EXPORT_DIRECTORY -ErrorAction SilentlyContinue
    Remove-Item $pfxPath, $cerPath -Force -ErrorAction SilentlyContinue
    $password = $null
}
