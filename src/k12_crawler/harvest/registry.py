"""Site registry for the raw harvester.

Adding a site = add a module in ``harvest/sites/`` and one entry in ``SITES``.
The CLI, ``sites`` catalogue, and runner all read from here.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type, Union

from ..subjects import Subject, catalog_entry, resolve_in
from .site import Site
from .sites import BaiduSite, CoocoSite, JyeooSite

SITES: Dict[str, Type[Site]] = {
    BaiduSite.name: BaiduSite,
    CoocoSite.name: CoocoSite,
    JyeooSite.name: JyeooSite,
}


def available_sites() -> List[str]:
    return sorted(SITES)


def get_site(name: str) -> Type[Site]:
    try:
        return SITES[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown site: {name}; available: {', '.join(available_sites())}"
        ) from exc


def build_site(
    name: str, subject: Optional[Union[str, Subject]] = None
) -> Site:
    cls = get_site(name)
    if isinstance(subject, Subject):
        chosen = subject
    else:
        chosen = resolve_in(cls.subjects, subject)
    return cls(chosen)


def site_subjects(name: str):
    return get_site(name).subjects


def site_catalog(subject: Optional[str] = None) -> List[Dict[str, object]]:
    """Describe every site and the subject channels it supports."""
    catalog: List[Dict[str, object]] = []
    for name in available_sites():
        cls = SITES[name]
        subjects = list(cls.subjects)
        if subject:
            try:
                chosen = resolve_in(subjects, subject)
            except ValueError:
                continue
            subjects = [chosen]
        if not subjects:
            continue
        default = subjects[0] if subject else resolve_in(cls.subjects, None)
        catalog.append(
            {
                "name": name,
                "label": cls.label,
                "default_subject": default.code,
                "default_seed": cls.seed_for(default),
                "subject_count": len(subjects),
                "subjects": [catalog_entry(name, item) for item in subjects],
            }
        )
    return catalog
