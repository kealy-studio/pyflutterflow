import json
from pyflutterflow import PyFlutterflow
from pyflutterflow.BaseModels import AppBaseModel


class DeepLink(AppBaseModel):
    ff_page: str
    deep_link_parameter_name: str | None = None
    destination_id: str | None = None
    name: str | None = None

    @property
    def ff_route(self) -> dict | None:
        if self.ff_page and self.deep_link_parameter_name and self.destination_id:
            return {
                "initialPageName": self.ff_page,
                "parameterData": json.dumps({
                    self.deep_link_parameter_name: str(self.destination_id)
                })
             }
        elif self.ff_page:
            return {
                "initialPageName": self.ff_page
            }

    @property
    def ff_route_uri(self) -> str | None:
        settings = PyFlutterflow().get_environment()
        if self.destination_id and self.ff_page:
            return f"{settings.deep_link_uri}/{self.ff_page}/{str(self.destination_id)}"
        elif self.ff_page:
            return f"{settings.deep_link_uri}/{self.ff_page}"
        else:
            return None


class UserNotificationsRequest(AppBaseModel):
    recipient_ids: list[str]
    title: str
    body: str
    deeplink_page_name: str | None = None


class Notification(AppBaseModel):
    title: str
    body: str
    image_url: str | None = None
    deep_link: DeepLink | None = None