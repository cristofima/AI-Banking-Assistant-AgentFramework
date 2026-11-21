from pydantic import BaseModel, Field, model_validator
from typing import Optional

from banking_transaction.models.conversation import CaseConversation, ConversationMessage


class Transaction(BaseModel):
    id: str
    description: Optional[str] = None
    #deposits, withdrawals, transfers, and payments
    type: Optional[str] = None
    # income/outcome
    flowType: Optional[str] = None
    recipientName: Optional[str] = None
    recipientBankReference: Optional[str] = None
    product_number: Optional[str] = None
    #BankTransfer,DirectDebit,CreditCard
    paymentType: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    timestamp: Optional[str] = None
    country: str | None = None
    city: str | None = None
    originalTransactionId: str | None = None
    supportCaseId: str | None = None
    sourceKind: str = "source"
    category: Optional[str] = None
    #paid, pending, failed
    status: Optional[str] = None


class TransactionPage(BaseModel):
    items: list[Transaction]
    total: int
    limit: int
    offset: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None



class DisputeCase(BaseModel):
    caseId: str
    productNumber: Optional[str] = None
    transactionId: str
    reason: str
    status: str
    triageOutcome: Optional[str] = None
    resolutionOutcome: Optional[str] = None
    resolutionNotes: Optional[str] = None
    financialEffectsStatus: str = "NOT_EXECUTED"
    cardProtectionStatus: str = "NOT_BLOCKED"
    caseVersion: int = 0
    verdict: str | None = None
    rationale: str | None = None
    effectCode: str | None = None
    effects: dict[str, str] | None = None
    cardProtection: dict[str, object] | None = None
    recommendationType: Optional[str] = None
    recommendationRationale: Optional[str] = None
    recommendationOptedOut: bool = False
    openedAt: str
    updatedAt: str
    resolvedAt: Optional[str] = None


class DisputeCaseEvent(BaseModel):
    eventType: str
    actor: str
    message: Optional[str] = None
    displayMessage: Optional[str] = None
    createdAt: str


class OpenDisputeRequest(BaseModel):
    transactionId: str
    reason: str


class AcceptDisputeRequest(BaseModel):
    previewToken: str
    conversationHistory: list[ConversationMessage] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_conversation_history(self) -> "AcceptDisputeRequest":
        CaseConversation(messages=self.conversationHistory or [])
        return self


class DisputePreview(BaseModel):
    previewToken: str
    transactionId: str
    reason: str
    expiresAt: str
    transaction: Transaction


class DisputeApprovalRequest(BaseModel):
    approved: bool


class ResolveCaseRequest(BaseModel):
    resolutionOutcome: str
    resolutionNotes: Optional[str] = None
