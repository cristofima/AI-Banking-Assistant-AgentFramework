import yaml
from pathlib import Path
from typing import Any


def get_logging_config_path() -> Path | None:
    """Get the path to the logging configuration file.

    Returns:
        Path to the logging configuration file, or None if not found.
    """

    # Get the project root directory (two levels up from this file)
    config_dir =  Path(__file__).parent.parent

    default_config = config_dir.joinpath("logging-default.yaml")

    if default_config.is_file():
        return default_config
    else:
        # If no config files exist, return None to use basic config
        return None


def _basic_logging_config() -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "formatter": "default",
                "stream": "ext://sys.stdout"
            }
        },
        "root": {
            "level": "INFO",
            "handlers": ["console"]
        }
    }


def load_logging_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load logging configuration from YAML file.

    Args:
        config_path: Path to the logging configuration file.

    Returns:
        Dictionary containing logging configuration.
    """
    if config_path is None:
        config_path = get_logging_config_path()

    if config_path is None or not config_path.exists():
        # Return basic configuration if no file found
        return _basic_logging_config()

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict) or config.get("version") != 1:
            return _basic_logging_config()
        return config
    except (OSError, yaml.YAMLError) as e:
        print(f"Error loading logging config from {config_path}: {e}")
        # Return basic configuration on error
        return _basic_logging_config()
