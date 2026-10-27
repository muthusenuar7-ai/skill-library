"""Identity/content regressions for the collection POST -> streamed ZIP workflow."""
import io
import zipfile
from unittest.mock import patch

from django.test import TestCase, override_settings

from library import exports, views
from library.models import Category, Skill


class ExportRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        domains = [Category.objects.create(name=name) for name in ("Finance", "Analytics")]
        cls.skills = [Skill.objects.create(
            title=f"Export Skill {i:02d}", category=domains[i % 2], status="published",
            content=f"# Skill identity {i}\n\nComplete Markdown — café\n\n```python\nprint({i})\n```\n",
        ) for i in range(25)]

    def post(self, ids):
        return self.client.post("/collection/download/", {"ids": ids})

    def assert_archive(self, response, skills, filenames):
        self.assertEqual(response.status_code, 200)
        data = b"".join(response.streaming_content)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), set(filenames))
            self.assertEqual(len(archive.infolist()), len(skills))
            content_ids = {skill.content.encode("utf-8"): skill.pk for skill in skills}
            actual_ids = {content_ids[archive.read(name)] for name in archive.namelist()}
            for skill, name in zip(skills, filenames):
                self.assertNotIn("/", name)
                self.assertNotIn(" ", name)
                self.assertEqual(archive.read(name), skill.content.encode("utf-8"))
            self.assertEqual(actual_ids, {s.pk for s in skills})
        response.close()

    def test_all_requested_selection_sizes(self):
        for count in (1, 2, 3, 4, 6, 8, 9, 10, 25):
            with self.subTest(count=count):
                skills = self.skills[:count]
                self.assert_archive(self.post([s.pk for s in skills]), skills,
                                    [f"Export-Skill-{i:02d}.md" for i in range(count)])

    def test_nine_selected_ids_survive_every_server_stage(self):
        skills = self.skills[:9]
        ids = [s.pk for s in skills]
        with patch.object(views, "_public_by_ids", wraps=views._public_by_ids) as query, \
             patch.object(exports, "selection_zip", wraps=exports.selection_zip) as build:
            response = self.post(",".join(map(str, ids)))
            self.assertEqual(query.call_args.args[0], ids)
            self.assertEqual([s.pk for s in build.call_args.args[0]], ids)
            self.assert_archive(response, skills, [f"Export-Skill-{i:02d}.md" for i in range(9)])

    def test_case_spacing_punctuation_collisions_and_database_only_content(self):
        skills = self.skills[:3]
        for skill, title in zip(skills, ("Same Title", "same  title", "Same Title!")):
            skill.title = title
            skill.save()
            self.assertFalse(skill.attachments.exists())
        self.assert_archive(self.post([s.pk for s in skills]), skills, [
            "Same-Title.md", f"same-title-SK{skills[1].pk:04d}.md", f"Same-Title-SK{skills[2].pk:04d}.md",
        ])

    def test_repeated_and_comma_separated_fields_deduplicate_by_id(self):
        a, b = self.skills[:2]
        self.assert_archive(self.post([f"{a.pk},{b.pk}", str(a.pk)]), [a, b],
                            ["Export-Skill-00.md", "Export-Skill-01.md"])

    def test_nine_selected_one_unpublished_must_not_silently_export_eight(self):
        skills = self.skills[:9]
        Skill.objects.filter(pk=skills[-1].pk).update(status="draft")
        response = self.post([s.pk for s in skills])
        self.assertRedirects(response, "/collection/", fetch_redirect_response=False)
        self.assertIn("1 selected skill", str(list(response.wsgi_request._messages)[-1]))
        self.assertNotIn("Content-Disposition", response)

    def test_unavailable_ids_are_rejected_without_private_details(self):
        for change in ("draft", "archived", "inactive-domain", "deleted"):
            with self.subTest(change=change):
                extra = Skill.objects.create(title="Private title", category=self.skills[0].category,
                                             status="published", content="PRIVATE CONTENT")
                pk = extra.pk
                if change == "inactive-domain":
                    hidden = Category.objects.create(name="Hidden", is_active=False)
                    Skill.objects.filter(pk=pk).update(category=hidden)
                elif change == "deleted":
                    extra.delete()
                else:
                    Skill.objects.filter(pk=pk).update(status=change)
                response = self.post([self.skills[0].pk, pk])
                self.assertEqual(response.status_code, 302)
                message = str(list(response.wsgi_request._messages)[-1])
                self.assertIn("No ZIP was downloaded", message)
                self.assertNotIn("Private title", message)
                self.assertNotIn("PRIVATE CONTENT", message)

    def test_malformed_ids_never_silently_disappear(self):
        for bad in ("abc", "-1", "0", "1.5", "999999999999999999999999", "１", ""):
            with self.subTest(bad=bad):
                response = self.post([self.skills[0].pk, bad])
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("Content-Disposition", response)

    @override_settings(COLLECTION_MAX_SKILLS=8)
    def test_over_limit_is_an_error_not_a_truncated_zip(self):
        self.assertEqual(self.post([s.pk for s in self.skills[:9]]).status_code, 302)

    def test_more_than_2000_duplicate_tokens_cannot_hide_the_last_id(self):
        a, b = self.skills[:2]
        self.assert_archive(self.post(",".join([str(a.pk)] * 2001 + [str(b.pk)])), [a, b],
                            ["Export-Skill-00.md", "Export-Skill-01.md"])
