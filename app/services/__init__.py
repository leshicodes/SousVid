from app.services.base import BaseService
from app.services.mealie import MealieService

def get_service_instance(service_data: dict) -> BaseService:
    """Instantiate a BaseService subclass from a database record dict."""
    stype = service_data.get("type")
    if stype == "mealie":
        return MealieService(
            service_id=service_data["id"],
            name=service_data["name"],
            url=service_data["url"],
            api_token=service_data["api_token"],
            ssl_verify=bool(service_data.get("ssl_verify", 1))
        )
    else:
        raise ValueError(f"Unsupported service type: {stype}")
