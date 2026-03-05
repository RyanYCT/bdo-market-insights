"""
Hypothesis settings for property-based testing.

This module configures Hypothesis profiles for different testing environments.
"""

from hypothesis import settings, Verbosity, Phase

# Default profile for local development
settings.register_profile(
    "dev",
    max_examples=100,
    verbosity=Verbosity.normal,
    deadline=None,
    print_blob=True,
)

# CI profile with more iterations and stricter settings
settings.register_profile(
    "ci",
    max_examples=200,
    verbosity=Verbosity.verbose,
    deadline=5000,  # 5 second deadline per test
    print_blob=True,
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.target, Phase.shrink],
)

# Debug profile for investigating failures
settings.register_profile(
    "debug",
    max_examples=10,
    verbosity=Verbosity.debug,
    deadline=None,
    print_blob=True,
)

# Load the appropriate profile based on environment
import os
profile = os.getenv("HYPOTHESIS_PROFILE", "dev")
settings.load_profile(profile)
