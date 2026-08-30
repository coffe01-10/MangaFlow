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
    supported_parameters: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        value = asdict(self)
        for key in (
            "operations",
            "resolutions",
            "preview_resolutions",
            "regions",
            "supported_parameters",
        ):
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
            # Request metadata the native text adapter actually consumes
            # (app/model_adapters/vertex.py). Declared here so callers can
            # negotiate parameters from capability data instead of hardcoding.
            supported_parameters=("max_output_tokens", "thinking_budget"),
        ),
        "image.nano_banana_2": ModelCapability(
            provider="vertex-ai",
            model_id=settings.vertex_image_model_nano_banana_2,
            logical_alias="image.nano_banana_2",
            display_name="Nano Banana 2",
            operations=("generate", "edit", "multi_turn_edit"),
            resolutions=("1K", "2K", "4K"),
            preview_resolutions=("4K",),
            max_reference_images=14,
        ),
        "image.nano_banana_pro": ModelCapability(
            provider="vertex-ai",
            model_id=settings.vertex_image_model_nano_banana_pro,
            logical_alias="image.nano_banana_pro",
            display_name="Nano Banana Pro",
            operations=("generate", "edit", "multi_turn_edit"),
            resolutions=("1K", "2K", "4K"),
            preview_resolutions=("4K",),
            max_reference_images=14,
        ),
    }
