"""Request dependencies."""

from datetime import date

from fastapi import Request
from sqlalchemy.orm import Session

from watson.dal.filters import PortfolioFilter
from watson.errors import AppError


def get_db(request: Request):
    session: Session = request.app.state.Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def filter_params(
    date_from: date | None = None,
    date_to: date | None = None,
    owner_id: str | None = None,
    pipeline: str | None = None,
    region: str | None = None,
    business_unit: str | None = None,
    product: str | None = None,
    customer_id: str | None = None,
    opportunity_id: str | None = None,
    stage: str | None = None,
) -> PortfolioFilter:
    if date_from and date_to and date_from > date_to:
        raise AppError(422, "The start date is after the end date.")
    return PortfolioFilter(
        date_from=date_from,
        date_to=date_to,
        owner_id=_clean(owner_id),
        pipeline=_clean(pipeline),
        region=_clean(region),
        business_unit=_clean(business_unit),
        product=_clean(product),
        customer_id=_clean(customer_id),
        opportunity_id=_clean(opportunity_id),
        stage=_clean(stage),
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
