from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


SEVERITY_RANK = {"normal": 0, "high": 1, "critical": 2}


@dataclass
class ModuleEntry:
    source_type: str
    reader_class_path: str
    analyzer_class_path: str
    sink_class_paths: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def reader_class(self):
        return _import_class(self.reader_class_path)

    def analyzer_class(self):
        return _import_class(self.analyzer_class_path)

    def sink_classes(self) -> list:
        return [_import_class(p) for p in self.sink_class_paths]


@dataclass
class TriggerSpec:
    source_type: str
    min_severity: str = "high"

    def matches(self, source_type: str, severity: str) -> bool:
        if self.source_type != source_type:
            return False
        return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(self.min_severity, 0)


@dataclass
class InvestigatorEntry:
    name: str
    class_path: str
    triggers: list[TriggerSpec] = field(default_factory=list)
    sink_class_paths: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def investigator_class(self):
        return _import_class(self.class_path)

    def sink_classes(self) -> list:
        return [_import_class(p) for p in self.sink_class_paths]

    def matches(self, source_type: str, severity: str) -> bool:
        return any(t.matches(source_type, severity) for t in self.triggers)


def _import_class(dotted_path: str):
    module_path, _, class_name = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid class path: {dotted_path!r}")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class Registry:
    def __init__(
        self,
        modules: dict[str, ModuleEntry],
        investigators: list[InvestigatorEntry] | None = None,
        remediation: dict[str, Any] | None = None,
        observability: dict[str, Any] | None = None,
    ):
        self._modules = modules
        self._investigators = investigators or []
        self._remediation = remediation or {}
        self._observability = observability or {}   # POINT 21 (Task #21)

    @classmethod
    def load(cls, config_path: str | Path) -> "Registry":
        path = Path(config_path)
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        sources = raw.get("sources") or {}
        modules: dict[str, ModuleEntry] = {}
        for source_type, entry in sources.items():
            if not isinstance(entry, dict):
                raise ValueError(f"Module config for {source_type!r} must be a mapping")
            reader_cp = entry.get("reader")
            analyzer_cp = entry.get("analyzer")
            sinks_cp = entry.get("sinks") or []
            if not reader_cp or not analyzer_cp:
                raise ValueError(
                    f"Module {source_type!r} must specify both 'reader' and 'analyzer' class paths"
                )
            if not isinstance(sinks_cp, list) or not sinks_cp:
                raise ValueError(
                    f"Module {source_type!r} must specify a non-empty 'sinks' list"
                )
            modules[source_type] = ModuleEntry(
                source_type=source_type,
                reader_class_path=reader_cp,
                analyzer_class_path=analyzer_cp,
                sink_class_paths=list(sinks_cp),
                config=entry.get("config") or {},
            )

        investigators_raw = raw.get("investigators") or {}
        investigators: list[InvestigatorEntry] = []
        for inv_name, inv_entry in investigators_raw.items():
            if not isinstance(inv_entry, dict):
                raise ValueError(f"Investigator config for {inv_name!r} must be a mapping")
            class_path = inv_entry.get("class")
            triggers = inv_entry.get("triggers") or []
            sinks_cp = inv_entry.get("sinks") or []
            if not class_path or not triggers or not sinks_cp:
                raise ValueError(
                    f"Investigator {inv_name!r} must specify 'class', 'triggers', and non-empty 'sinks'"
                )
            trigger_specs = [
                TriggerSpec(
                    source_type=t["source_type"],
                    min_severity=t.get("min_severity", "high"),
                )
                for t in triggers
            ]
            investigators.append(InvestigatorEntry(
                name=inv_name,
                class_path=class_path,
                triggers=trigger_specs,
                sink_class_paths=list(sinks_cp),
                config=inv_entry.get("config") or {},
            ))

        remediation_cfg = raw.get("remediation") or {}
        if not isinstance(remediation_cfg, dict):
            raise ValueError("`remediation` block in modules.yaml must be a mapping")

        # POINT 21 (Task #21): real-time stage observability config (optional block).
        observability_cfg = raw.get("observability") or {}
        if not isinstance(observability_cfg, dict):
            raise ValueError("`observability` block in modules.yaml must be a mapping")

        return cls(modules, investigators, remediation_cfg, observability_cfg)

    def source_types(self) -> list[str]:
        return list(self._modules.keys())

    def get(self, source_type: str) -> ModuleEntry:
        if source_type not in self._modules:
            raise KeyError(f"No module registered for source_type={source_type!r}")
        return self._modules[source_type]

    def build_reader(self, source_type: str, tenant_id: int):
        entry = self.get(source_type)
        cls = entry.reader_class()
        declared = getattr(cls, "source_type", "")
        if declared and declared != source_type:
            raise ValueError(
                f"Registry mismatch: YAML key {source_type!r} != "
                f"{cls.__name__}.source_type={declared!r}"
            )
        return cls(tenant_id=tenant_id, config=entry.config)

    def build_analyzer(self, source_type: str):
        entry = self.get(source_type)
        cls = entry.analyzer_class()
        return cls(config=entry.config)

    def build_sinks(self, source_type: str) -> list:
        entry = self.get(source_type)
        return [cls(config=entry.config) for cls in entry.sink_classes()]

    def investigators(self) -> list[InvestigatorEntry]:
        return list(self._investigators)

    def investigators_for(self, source_type: str, severity: str) -> list[InvestigatorEntry]:
        return [i for i in self._investigators if i.matches(source_type, severity)]

    def remediation_config(self) -> dict[str, Any]:
        return dict(self._remediation)

    def observability_config(self) -> dict[str, Any]:   # POINT 21 (Task #21)
        return dict(self._observability)
