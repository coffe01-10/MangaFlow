from dataclasses import asdict, dataclass

from app.config import Settings


@dataclass(frozen=True)
class ModelCapability:
    provider: str
    model_id: str
    logical_alias: str
    display_name: str
    operations: tuple[str, ...]
    resolutions: tuple[str, ...] = ()
    preview_resolutions: tuple[str, ...] = ()
    max_reference_images: int = 0
    regions: tuple[str, ...] = ("global",)

    def to_dict(self) -> dict:
        value = asdict(self)
        for key in ("operations", "resolutions", "preview_resolutions", "regions"):
            value[key] = list(value[key])
        return value


def build_registry(settings: Settings) -> dict[str, ModelCapability]:
    return {
        "text.fast": ModelCapability(
            provider="vertex-ai",
            model_id=settings.vertex_text_model,
            logical_alias="text.fast",
            display_name="Gemini 3.5 Flash",
            operations=("structured_text", "multimodal_analysis"),
        ),
        "image.fast": ModelCapability(
            provider="vertex-ai",
            model_id=settings.vertex_image_model_default,
            logical_alias="image.fast",
            display_name="Nano Banana 2",
            operations=("generate", "edit", "multi_turn_edit"),
            resolutions=("1K", "2K", "4K"),
            preview_resolutions=("4K",),
            max_reference_images=14,
        ),
        "image.quality": ModelCapability(
            provider="vertex-ai",
            model_id=settings.vertex_image_model_quality,
            logical_alias="image.quality",
            display_name="Nano Banana Pro",
            operations=("generate", "edit", "multi_turn_edit"),
            resolutions=("1K", "2K", "4K"),
            preview_resolutions=("4K",),
            max_reference_images=14,
        ),
    }
