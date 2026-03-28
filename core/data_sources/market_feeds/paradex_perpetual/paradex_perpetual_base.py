from ..funding_rate_feed_base import FundingRateFeedBase


class ParadexPerpetualBase(FundingRateFeedBase):
    """Base for Paradex perpetual feeds.

    API base: https://api.prod.paradex.trade/v1
    Markets summary endpoint: GET /markets/summary?market=ALL
    No authentication needed for public data.

    8H funding period: divide raw rate by 8 to get 1H rate.
    """

    _base_url = "https://api.prod.paradex.trade/v1"

    RATE_LIMITS = {
        "paradex_general": (120, 60),
    }

    def __init__(self, session=None):
        super().__init__(session)
        for limit_id, (max_req, window) in self.RATE_LIMITS.items():
            self.register_rate_limit(limit_id, max_req, window)
