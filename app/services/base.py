from abc import ABC, abstractmethod
from typing import Tuple, Any
from app.models import RecipeSchema

class BaseService(ABC):
    def __init__(self, service_id: str, name: str, url: str, api_token: str, ssl_verify: bool = True):
        self.service_id = service_id
        self.name = name
        self.url = url.rstrip("/")
        self.api_token = api_token
        self.ssl_verify = ssl_verify

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connection to the service.
        Returns a tuple of (success_boolean, message_string).
        """
        pass

    @abstractmethod
    def push_recipe(self, recipe: RecipeSchema, source_url: str | None, frames: list[str] | None) -> dict:
        """
        Push the extracted recipe to the destination service.
        Returns a dictionary containing the result:
        {
            "success": bool,
            "url": str or None,
            "slug": str or None,
            "error": str or None
        }
        """
        pass
