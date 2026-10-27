"""Read-only verification against existing published records; never changes the database.

Run from the Django directory:
  .venv/Scripts/python tools/verify_collection_export.py --ids 75,74,76,106,137,89,97,101,132
"""
import argparse
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_ENV", "development")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.test import Client, override_settings
from library import exports, views
from library.models import Skill


def verify(ids, output):
    trace = {"submitted_ids": ids}
    original_query, original_zip, original_add = views._public_by_ids, exports.selection_zip, exports._Zip.add

    def query(parsed):
        trace["parsed_ids"] = parsed
        skills = original_query(parsed)
        trace["database_ids"] = [s.pk for s in skills]
        return skills

    def build(skills):
        trace["generation_ids"] = [s.pk for s in skills]
        return original_zip(skills)

    written = []

    def add(writer, name, data):
        written.append(name)
        return original_add(writer, name, data)

    with override_settings(ALLOWED_HOSTS=["testserver"]), \
         patch.object(views, "_public_by_ids", side_effect=query), \
         patch.object(exports, "selection_zip", side_effect=build), \
         patch.object(exports._Zip, "add", autospec=True, side_effect=add):
        response = Client().post("/collection/download/", {"ids": ",".join(map(str, ids))})
        trace["received_fields"] = response.wsgi_request.POST.getlist("ids")
        assert response.status_code == 200, response.status_code
        data = b"".join(response.streaming_content)
        response.close()
    expected = original_query(ids)
    by_name = dict((name, skill) for skill, name in exports.assign_filenames(expected))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert len(archive.infolist()) == len(set(ids))
        assert set(archive.namelist()) == set(by_name)
        entries = []
        for name in archive.namelist():
            skill = by_name[name]
            assert archive.read(name) == skill.content.encode("utf-8")
            assert "/" not in name and " " not in name and name.endswith(".md")
            entries.append({"id": skill.pk, "filename": name, "exact_content": True})
    trace["written_filenames"] = written
    trace["entries"] = entries
    trace["exported_ids"] = [entry["id"] for entry in entries]
    for stage in ("parsed_ids", "database_ids", "generation_ids", "exported_ids"):
        assert set(trace[stage]) == set(ids), stage
    output.write_bytes(data)
    return trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--output", default="docs/export-verification")
    args = parser.parse_args()
    ids = list(dict.fromkeys(int(part) for part in args.ids.split(",")))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    report = {"nine_skill_trace": verify(ids, output / "verified-nine.zip"), "sizes": {}}
    available = list(Skill.objects.public().order_by("pk").values_list("pk", flat=True)[:25])
    assert len(available) >= 25, "Verification needs at least 25 existing published skills."
    for count in (1, 2, 3, 4, 6, 8, 9, 10, 25):
        trace = verify(available[:count], output / f"verified-{count}.zip")
        report["sizes"][str(count)] = trace
        print(f"{count} selected -> {len(trace['entries'])} exact Markdown files: PASS")
    (output / "trace.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"ID-by-ID evidence: {output / 'trace.json'}")


if __name__ == "__main__":
    main()
