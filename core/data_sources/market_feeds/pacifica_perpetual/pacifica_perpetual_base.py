from ..funding_rate_feed_base import FundingRateFeedBase


NORM_MAP = {"kBONK": "1000BONK", "kPEPE": "1000PEPE"}


class PacificaPerpetualBase(FundingRateFeedBase):
    _base_url = "https://api.pacifica.fi/api/v1"

    RATE_LIMITS = {
        "pacifica_general": (120, 60),
    }

    def __init__(self, session=None):
        super().__init__(session)
        for limit_id, (max_req, window) in self.RATE_LIMITS.items():
            self.register_rate_limit(limit_id, max_req, window)

    @staticmethod
    def _norm(sym: str) -> str:
        return NORM_MAP.get(sym, sym).upper()
