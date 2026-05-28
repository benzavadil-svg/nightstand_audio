from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image

from app.models import RenderState


class DisplayAdapter(ABC):
    @abstractmethod
    def render(self, state: RenderState, reason: str | None = None) -> None:
        raise NotImplementedError

    def tick(self) -> None:
        pass

    def sleep(self) -> None:
        pass

    def shutdown(self) -> None:
        self.sleep()


class ImageDisplayAdapter:
    def render(self, image: Image.Image) -> None:
        raise NotImplementedError

    def render_path(
        self,
        path: str,
        update_mode: str = "full",
        reason: str | None = None,
        clean_refresh: bool = False,
        region=None,
    ) -> None:
        with Image.open(path) as image:
            if update_mode == "partial":
                self.partial_update(image, region=region, reason=reason)
            else:
                self.full_update(image, reason=reason, clean_refresh=clean_refresh)

    def full_update(
        self,
        image: Image.Image,
        reason: str | None = None,
        clean_refresh: bool = False,
    ) -> None:
        self.render(image)

    def partial_update(self, image: Image.Image, region=None, reason: str | None = None) -> None:
        self.full_update(image)

    def one_shot_render_path(
        self,
        path: str,
        reason: str | None = None,
        displayed_hash: str | None = None,
    ) -> bool:
        self.render_path(path, update_mode="full", reason=reason, clean_refresh=True)
        return True

    def sleep(self) -> None:
        pass

    def shutdown(self) -> None:
        self.sleep()
