from ..funding_rate_feed_base import FundingRateFeedBase


class ExtendedPerpetualBase(FundingRateFeedBase):
    _base_url = "https://api.starknet.extended.exchange/api/v1"

    RATE_LIMITS = {
        "extended_general": (120, 60),  # conservative: 120 req/min
    }

    def __init__(self, session=None):
        super().__init__(session)
        for limit_id, (max_req, window) in self.RATE_LIMITS.items():
            self.register_rate_limit(limit_id, max_req, window)

    @staticmethod
    def _norm(asset_name: str) -> str:
        s = asset_name.upper()
        for suffix in ("-USD", "-USDC", "-USDT"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
        return s
