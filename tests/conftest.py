"""
Shared pytest fixtures for BDO Market Insights tests.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Mock psycopg2 before any imports that might use it
sys.modules['psycopg2'] = MagicMock()
sys.modules['psycopg2.pool'] = MagicMock()
sys.modules['psycopg2.extras'] = MagicMock()

# Add lambda_layer to Python path for common module imports
lambda_layer_path = Path(__file__).parent.parent / "lambda_layer" / "python"
sys.path.insert(0, str(lambda_layer_path))

# Add src directory to Python path for Lambda function imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

# Add tests directory to Python path for imports
tests_path = Path(__file__).parent
sys.path.insert(0, str(tests_path))

# Load Hypothesis settings
from hypothesis_settings import settings
