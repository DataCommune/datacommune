"""Pipeline registry for dataset discovery.

Concrete pipelines register themselves at import time via the
@register_pipeline decorator so the runner and API layer can
enumerate available datasets without hard-coding.

Usage in a pipeline module:

    from app.etl.registry import register_pipeline

    @register_pipeline("monuments")
    class MonumentsPipeline(BasePipeline):
        ...
"""

from app.etl.base import BasePipeline


class PipelineRegistry:
    """Singleton-style registry mapping dataset names to pipeline classes."""

    def __init__(self) -> None:
        self._pipelines: dict[str, type[BasePipeline]] = {}

    def register(
        self,
        name_or_cls: str | type[BasePipeline],
        pipeline_cls: type[BasePipeline] | None = None,
    ) -> None:
        """Register a pipeline class.

        Supports two call styles:
          registry.register("name", PipelineCls)   # explicit name
          registry.register(PipelineCls)            # infer from dataset_name property
        """
        if pipeline_cls is not None:
            # Explicit name
            assert isinstance(name_or_cls, str)
            self._pipelines[name_or_cls] = pipeline_cls
        else:
            # Infer name from class
            cls = name_or_cls
            assert isinstance(cls, type)
            inst = cls.__new__(cls)  # type: ignore[arg-type]
            name = inst.dataset_name
            self._pipelines[name] = cls  # type: ignore[assignment]

    def get(self, name: str) -> type[BasePipeline] | None:
        """Return the pipeline class for *name*, or None."""
        return self._pipelines.get(name)

    def list_datasets(self) -> list[str]:
        """Return all registered dataset names."""
        return list(self._pipelines.keys())

    def get_all(self) -> dict[str, type[BasePipeline]]:
        """Return the full pipeline map (for scheduler/runner)."""
        return dict(self._pipelines)


# Module-level singleton used across the application.
registry = PipelineRegistry()


def register_pipeline(name: str):
    """Class decorator that auto-registers a pipeline in the global registry.

    @register_pipeline("monuments")
    class MonumentsPipeline(BasePipeline):
        ...
    """

    def decorator(cls: type[BasePipeline]) -> type[BasePipeline]:
        registry.register(name, cls)
        return cls

    return decorator
