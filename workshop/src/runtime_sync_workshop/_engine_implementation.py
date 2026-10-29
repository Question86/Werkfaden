from __future__ import annotations

from ._core_state import StateCore
from ._core_checkout import CheckoutCore
from ._core_metadata import MetadataCore
from ._core_prepare import PrepareCore
from ._core_verify import VerifyCore
from ._core_apply import ApplyCore
from ._core_recovery import RecoveryCore
from ._core_scientific import ScientificCore
from ._core_state import (
    PRE_APPLY_STATES as PRE_APPLY_STATES,
    TERMINAL_STATES as TERMINAL_STATES,
    re_fullmatch_transaction as re_fullmatch_transaction,
)


class WorkshopEngine(StateCore, CheckoutCore, MetadataCore, PrepareCore, VerifyCore, ApplyCore, RecoveryCore, ScientificCore):
    """Private composition base; instantiate the guarded public engine only."""
