"""Portfolio filters shared by the dashboard views."""

from dataclasses import dataclass
from datetime import date


@dataclass
class PortfolioFilter:
    date_from: date | None = None
    date_to: date | None = None
    owner_id: str | None = None
    pipeline: str | None = None
    region: str | None = None
    business_unit: str | None = None
    product: str | None = None
    customer_id: str | None = None
    opportunity_id: str | None = None
    stage: str | None = None

    def any(self) -> bool:
        return any(
            (
                self.date_from,
                self.date_to,
                self.owner_id,
                self.pipeline,
                self.region,
                self.business_unit,
                self.product,
                self.customer_id,
                self.opportunity_id,
                self.stage,
            )
        )

    def restricts_deals(self) -> bool:
        return any(
            (
                self.date_from,
                self.date_to,
                self.pipeline,
                self.product,
                self.stage,
                self.opportunity_id,
            )
        )
