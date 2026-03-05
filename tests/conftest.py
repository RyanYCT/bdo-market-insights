"""
Shared pytest fixtures for BDO Market Insights tests.
"""

import pytest
import sys
from pathlib import Path

# Add lambda_layer to Python path for imports
lambda_layer_path = Path(__file__).parent.parent / "lambda_layer" / "python"
sys.path.insert(0, str(lambda_layer_path))

# Load Hypothesis settings
from tests.hypothesis_settings import settings
