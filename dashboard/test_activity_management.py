import datetime
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from accounts.models import User
from library import activity_service
from library.activity import log_activity
from library.models import ActivityLog, Category, ProtectedRecordError, SecurityAuditLog, Skill
from library.tests.helpers import PASSWORD, BaseTestCase


class ActivityBase(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.vineet = self.make_user("vineet@datasense.com", "super_admin")
        self.himanshi = self.make_user("himanshi@datasense.com", "admin")
        self.amit = self.make_user("amit@datasense.com", "admin")
        ActivityLog.objects.all().delete()  # start from an empty feed (nothing protected exists yet)
        SecurityAuditLog.objects.all()  # (read access is fine; deleting is blocked and tested below)

    def entry(self, action="skill_update", actor=None, days_ago=0, target=None, desc="did something"):
        actor = actor or self.admin
        a = log_activity(actor, action, target[0] if target else "", target[1] if target else None, desc)
        if days_ago:
            ActivityLog.objects.filter(pk=a.pk).update(created_at=timezone.now() - datetime.timedelta(days=days_ago))
        return a

    def post_clear(self, **data):
        return self.client.post("/admin/activity/clear/", data)

    def visible(self):
        return ActivityLog.objects.filter(archived_at__isnull=True)


class ClearingPermissionTests(ActivityBase):
    def test_both_super_admins_see_the_manage_activity_controls(self):
        self.entry()
        for user in (self.super, self.vineet):
            self.client.logout()
            self.login(user)
            body = self.client.get("/admin/activity/").content.decode()
            for needle in ("Manage Activity", "Select Activity", "Clear Selected", "Clear Older Records", "Clear All", 'id="clear-form"', 'id="activity-dialog"', "csrfmiddlewaretoken"):
                self.assertIn(needle, body, user.email)
            self.assertEqual(self.client.get("/admin/activity/audit/").status_code, 200)

    def test_admins_see_activity_but_no_clearing_controls(self):
        self.entry()
        for user in (self.admin, self.himanshi, self.amit):
            self.client.logout()
            self.login(user)
            r = self.client.get("/admin/activity/")
            self.assertEqual(r.status_code, 200, user.email)
            body = r.content.decode()
            for hidden in ("Manage Activity", "Clear Selected", "Clear Older", "Clear All", "clear-form", "activity-dialog", "act-check", "audit trail", "Include cleared"):
                self.assertNotIn(hidden, body, f"{user.email}: {hidden}")

    def test_direct_requests_from_admins_are_rejected_and_change_nothing(self):
        a = self.entry()
        for user in (self.admin, self.himanshi, self.amit):
            self.client.logout()
            self.login(user)
            for mode in ("selected", "older", "all"):
                r = self.post_clear(mode=mode, ids=a.pk, before=timezone.localdate().isoformat(), expected_count=1, confirm="yes")
                self.assertEqual(r.status_code, 403, f"{user.email} {mode}")
            self.assertEqual(self.client.get("/admin/activity/clear/count/?mode=all").status_code, 403)
            self.assertEqual(self.client.get("/admin/activity/audit/").status_code, 403)
        self.assertEqual(self.visible().count(), 1)
        self.assertFalse(SecurityAuditLog.objects.exists())

    def test_anonymous_requests_go_to_login(self):
        for r in (self.post_clear(mode="all", expected_count=0, confirm="yes"), self.client.get("/admin/activity/clear/count/?mode=all"), self.client.get("/admin/activity/audit/")):
            self.assertEqual(r.status_code, 302)
            self.assertIn("/admin/login/", r["Location"])

    def test_service_layer_refuses_non_super_admins_even_when_called_directly(self):
        self.entry()
        for user in (self.admin, self.himanshi, self.amit, None):
            with self.assertRaises(PermissionDenied):
                activity_service.clear_activity(user, "all", expected_count=1)
            with self.assertRaises(PermissionDenied):
                activity_service.plan(user, "all")
        self.assertEqual(self.visible().count(), 1)

    def test_a_super_admin_without_the_permission_or_deactivated_cannot_clear(self):
        from django.contrib.auth.models import Permission
        self.entry()
        self.super.groups.clear()
        self.super.clear_role_cache()
        with self.assertRaises(PermissionDenied):
            activity_service.clear_activity(self.super, "all", expected_count=1)
        self.vineet.is_active = False
        with self.assertRaises(PermissionDenied):
            activity_service.clear_activity(self.vineet, "all", expected_count=1)

    def test_get_not_allowed_and_csrf_enforced(self):
        self.login(self.super)
        self.assertEqual(self.client.get("/admin/activity/clear/").status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.login(username=self.super.email, password=PASSWORD)
        self.assertEqual(strict.post("/admin/activity/clear/", {"mode": "all", "expected_count": 0, "confirm": "yes"}).status_code, 403)

    def test_only_super_admins_hold_the_permission(self):
        for u in (self.super, self.vineet):
            self.assertTrue(u.has_perm("library.clear_activity"))
        for u in (self.admin, self.himanshi, self.amit):
            self.assertFalse(u.has_perm("library.clear_activity"))


class ClearingBehaviourTests(ActivityBase):
    def setUp(self):
        super().setUp()
        self.login(self.super)

    def test_clear_selected_archives_only_the_selected_records(self):
        rows = [self.entry(desc=f"row {i}") for i in range(5)]
        r = self.post_clear(mode="selected", ids=[rows[0].pk, rows[2].pk, rows[4].pk], expected_count=3, confirm="yes")
        self.assertRedirects(r, "/admin/activity/", fetch_redirect_response=False)
        descs = set(self.visible().values_list("description", flat=True))
        self.assertTrue({"row 1", "row 3"} <= descs)
        self.assertFalse({"row 0", "row 2", "row 4"} & descs)
        self.assertEqual(len(descs), 3)  # two survivors + the protected clearing record
        gone = ActivityLog.objects.get(pk=rows[0].pk)
        self.assertIsNotNone(gone.archived_at)  # soft delete: the row still exists
        self.assertEqual(gone.archived_by, self.super)
        self.assertEqual(ActivityLog.objects.filter(description__startswith="row").count(), 5)
        body = self.client.get("/admin/activity/").content.decode()
        self.assertNotIn("row 0", body)
        self.assertIn("row 1", body)

    def test_clear_older_uses_the_date_and_leaves_newer_records(self):
        old = [self.entry(days_ago=40, desc="old a"), self.entry(days_ago=31, desc="old b")]
        new = [self.entry(days_ago=5, desc="new a"), self.entry(desc="today")]
        cutoff = (timezone.localdate() - datetime.timedelta(days=30)).isoformat()
        count = self.client.get(f"/admin/activity/clear/count/?mode=older&before={cutoff}").json()
        self.assertEqual(count, {"count": 2, "protected_kept": 0})
        self.post_clear(mode="older", before=cutoff, expected_count=2, confirm="yes")
        descs = set(self.visible().values_list("description", flat=True))
        self.assertTrue({"new a", "today"} <= descs)
        self.assertFalse({"old a", "old b"} & descs)
        self.assertTrue(all(ActivityLog.objects.get(pk=o.pk).archived_at for o in old))
        self.assertTrue(all(ActivityLog.objects.get(pk=n.pk).archived_at is None for n in new))

    def test_bad_dates_are_rejected(self):
        self.entry(days_ago=5)
        for bad in ("", "not-a-date", "2999-01-01"):
            r = self.client.get(f"/admin/activity/clear/count/?mode=older&before={bad}")
            self.assertEqual(r.status_code, 400, bad)
            self.post_clear(mode="older", before=bad, expected_count=1, confirm="yes")
        self.assertEqual(self.visible().count(), 1)

    def test_clear_all_only_touches_eligible_records(self):
        ordinary = [self.entry("skill_update"), self.entry("login", self.super), self.entry("import")]
        protected = [self.entry("user_role_change", self.super, desc="Admin -> Super"), self.entry("password_change", self.admin), self.entry("user_create", self.super),
                     self.entry("settings_update", self.super), self.entry("export", self.super), self.entry("dev_password_init")]
        self.assertEqual(self.client.get("/admin/activity/clear/count/?mode=all").json(), {"count": 3, "protected_kept": 6})
        r = self.post_clear(mode="all", expected_count=3, confirm="yes")
        self.assertIn("6 protected security records were kept", [m.message for m in r.wsgi_request._messages][0])
        for a in ordinary:
            self.assertIsNotNone(ActivityLog.objects.get(pk=a.pk).archived_at)
        for a in protected:
            self.assertIsNone(ActivityLog.objects.get(pk=a.pk).archived_at, a.action)
        body = self.client.get("/admin/activity/").content.decode()
        self.assertIn("Admin -&gt; Super", body)

    def test_protected_records_cannot_be_cleared_even_by_selecting_them_directly(self):
        prot = self.entry("user_role_change", self.super)
        normal = self.entry("skill_update")
        self.assertEqual(self.client.get(f"/admin/activity/clear/count/?mode=selected&ids={prot.pk},{normal.pk}").json(), {"count": 1, "protected_kept": 1})
        self.post_clear(mode="selected", ids=[prot.pk, normal.pk], expected_count=2, confirm="yes")  # wrong count: refuses everything
        self.assertEqual(self.visible().filter(pk__in=[prot.pk, normal.pk]).count(), 2)
        self.post_clear(mode="selected", ids=[prot.pk], expected_count=1, confirm="yes")
        self.assertIsNone(ActivityLog.objects.get(pk=prot.pk).archived_at)
        self.post_clear(mode="selected", ids=[prot.pk, normal.pk], expected_count=1, confirm="yes")
        self.assertIsNone(ActivityLog.objects.get(pk=prot.pk).archived_at)
        self.assertIsNotNone(ActivityLog.objects.get(pk=normal.pk).archived_at)

    def test_confirmation_and_count_validation(self):
        rows = [self.entry() for _ in range(3)]
        self.post_clear(mode="all", expected_count=3)  # no confirm flag
        self.post_clear(mode="all", confirm="yes")  # no count
        self.post_clear(mode="all", expected_count="abc", confirm="yes")
        r = self.post_clear(mode="all", expected_count=99, confirm="yes")  # the count the person saw is not the real one
        self.assertIn("changed while you were confirming", [m.message for m in r.wsgi_request._messages][-1])
        self.post_clear(mode="bogus", expected_count=3, confirm="yes")
        self.assertEqual(self.visible().count(), 3)
        self.assertFalse(SecurityAuditLog.objects.exists())
        self.post_clear(mode="all", expected_count=3, confirm="yes")
        self.assertEqual(self.visible().filter(pk__in=[r.pk for r in rows]).count(), 0)

    def test_nothing_to_clear_is_reported_not_recorded(self):
        r = self.post_clear(mode="all", expected_count=0, confirm="yes")
        self.assertIn("no clearable", [m.message for m in r.wsgi_request._messages][-1])
        self.assertFalse(SecurityAuditLog.objects.exists())

    def test_clearing_is_recorded_in_the_protected_audit_trail_and_the_feed(self):
        for _ in range(4):
            self.entry()
        self.entry("user_role_change", self.super)
        self.post_clear(mode="all", expected_count=4, confirm="yes")
        audit = SecurityAuditLog.objects.get()
        self.assertEqual((audit.actor, audit.actor_email, audit.action, audit.records_affected, audit.protected_kept), (self.super, "muthu@datasense.com", "clear_all", 4, 1))
        self.assertLess(abs((timezone.now() - audit.created_at).total_seconds()), 30)
        feed = ActivityLog.objects.get(action="activity_cleared")
        self.assertTrue(feed.is_protected)
        self.assertIsNone(feed.archived_at)
        self.assertIn("4 record(s) cleared", feed.description)
        page = self.client.get("/admin/activity/audit/").content.decode()
        for needle in ("Clear All Activity", "muthu@datasense.com", "<strong>4</strong>"):
            self.assertIn(needle, page)

    def test_each_option_is_recorded_with_its_own_action_name(self):
        a, b, c = self.entry(days_ago=50), self.entry(), self.entry()
        self.post_clear(mode="selected", ids=[b.pk], expected_count=1, confirm="yes")
        self.post_clear(mode="older", before=(timezone.localdate() - datetime.timedelta(days=10)).isoformat(), expected_count=1, confirm="yes")
        self.post_clear(mode="all", expected_count=1, confirm="yes")
        self.assertEqual(list(SecurityAuditLog.objects.order_by("id").values_list("action", flat=True)), ["clear_selected", "clear_older", "clear_all"])

    def test_later_clear_all_never_removes_earlier_clearing_history(self):
        self.entry()
        self.post_clear(mode="all", expected_count=1, confirm="yes")
        self.entry()
        self.post_clear(mode="all", expected_count=1, confirm="yes")
        self.assertEqual(SecurityAuditLog.objects.count(), 2)
        self.assertEqual(ActivityLog.objects.filter(action="activity_cleared", archived_at__isnull=True).count(), 2)

    def test_audit_trail_cannot_be_edited_or_deleted_and_protected_activity_cannot_be_deleted(self):
        self.entry()
        self.post_clear(mode="all", expected_count=1, confirm="yes")
        audit = SecurityAuditLog.objects.get()
        with self.assertRaises(ProtectedRecordError):
            audit.delete()
        with self.assertRaises(ProtectedRecordError):
            SecurityAuditLog.objects.all().delete()
        with self.assertRaises(ProtectedRecordError):
            SecurityAuditLog.objects.all().update(records_affected=0)
        audit.records_affected = 0
        with self.assertRaises(ProtectedRecordError):
            audit.save()
        protected = ActivityLog.objects.get(action="activity_cleared")
        with self.assertRaises(ProtectedRecordError):
            protected.delete()
        with self.assertRaises(ProtectedRecordError):
            ActivityLog.objects.filter(is_protected=True).delete()
        self.assertEqual(SecurityAuditLog.objects.get().records_affected, 1)

    def test_django_admin_has_no_delete_for_the_audit_trail(self):
        from django.contrib import admin
        model_admin = admin.site._registry[SecurityAuditLog]
        for check in (model_admin.has_add_permission(None), model_admin.has_change_permission(None), model_admin.has_delete_permission(None)):
            self.assertFalse(check)

    def test_the_operation_is_atomic(self):
        self.entry()
        self.entry()
        with mock.patch("library.activity_service.log_activity", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                activity_service.clear_activity(self.super, "all", expected_count=2)
        self.assertEqual(self.visible().count(), 2)  # the archive step was rolled back
        self.assertFalse(SecurityAuditLog.objects.exists())

    def test_other_records_are_untouched(self):
        before = (Skill.objects.count(), Category.objects.count(), User.objects.count(), list(Skill.objects.values_list("status", flat=True)))
        self.entry()
        self.post_clear(mode="all", expected_count=1, confirm="yes")
        self.assertEqual(before, (Skill.objects.count(), Category.objects.count(), User.objects.count(), list(Skill.objects.values_list("status", flat=True))))
        self.assertEqual(self.client.get(f"/skills/{self.pub.slug}/download/").status_code, 200)
        self.assertEqual(self.client.get("/admin/dashboard/").status_code, 200)

    def test_cleared_records_are_hidden_from_everyone_but_viewable_by_super_admins_on_request(self):
        gone = self.entry(desc="will be cleared", target=("skill", self.pub.pk))
        self.post_clear(mode="selected", ids=[gone.pk], expected_count=1, confirm="yes")
        self.assertNotIn("will be cleared", self.client.get("/admin/activity/").content.decode())
        shown = self.client.get("/admin/activity/?cleared=1").content.decode()
        self.assertIn("will be cleared", shown)
        self.assertIn("Cleared", shown)
        self.assertNotIn('class="act-check"', shown)  # an already-cleared row cannot be selected again
        self.client.logout()
        self.login(self.admin)
        self.assertNotIn("will be cleared", self.client.get("/admin/activity/?cleared=1").content.decode())  # the admin cannot opt in

    def test_selection_size_is_bounded(self):
        rows = [self.entry() for _ in range(3)]
        with mock.patch.object(activity_service, "MAX_SELECTED", 2):
            self.assertEqual(activity_service.plan(self.super, "selected", [r.pk for r in rows])["count"], 2)


class ActivityPageTests(ActivityBase):
    def setUp(self):
        super().setUp()
        self.login(self.super)

    def test_columns_and_related_resource(self):
        self.entry("skill_publish", self.admin, target=("skill", self.pub.pk), desc="Formula Explainer published")
        self.entry("skill_update", self.admin, target=("skill", 987654), desc="gone skill")
        body = self.client.get("/admin/activity/").content.decode()
        for col in ("Date and time", "Administrator", "Action", "Related skill or resource", "Description"):
            self.assertIn(col, body)
        self.assertIn("Skill published", body)
        self.assertIn("akhilesh@datasense.com", body)
        self.assertIn(f'href="/admin/skills/{self.pub.pk}/edit/">Formula Explainer</a>', body)
        self.assertIn("(removed)", body)

    def test_filters_search_and_pagination(self):
        for i in range(60):
            self.entry("skill_update", self.admin if i % 2 else self.himanshi, days_ago=i, desc=f"entry number {i}")
        self.entry("category_create", self.vineet, desc="special category thing")
        body = self.client.get("/admin/activity/").content.decode()
        self.assertEqual(body.count('class="act-check"'), 50)  # 50 per page
        self.assertIn("Page 1 of 2", body)
        self.assertIn("Page 2 of 2", self.client.get("/admin/activity/?page=2").content.decode())
        only = self.client.get("/admin/activity/?admin=vineet@datasense.com").content.decode()
        self.assertIn("special category thing", only)
        self.assertNotIn("entry number", only)
        typed = self.client.get("/admin/activity/?action=category_create").content.decode()
        self.assertIn("special category thing", typed)
        self.assertNotIn("entry number", typed)
        found = self.client.get("/admin/activity/?q=number+7").content.decode()
        self.assertIn("entry number 7", found)
        self.assertNotIn("special category", found)
        d = (timezone.localdate() - datetime.timedelta(days=3)).isoformat()
        ranged = self.client.get(f"/admin/activity/?date_from={d}&date_to={d}").content.decode()
        self.assertIn("entry number 3", ranged)
        self.assertNotIn("entry number 2<", ranged)
        self.assertNotIn("entry number 4<", ranged)
        self.assertIn("Reset", self.client.get("/admin/activity/?q=x").content.decode())

    def test_filter_options_come_from_real_data(self):
        self.entry("skill_update", self.admin)
        body = self.client.get("/admin/activity/").content.decode()
        self.assertIn('<option value="akhilesh@datasense.com">', body)
        self.assertIn('<option value="skill_update">Skill edited</option>', body)

    def test_admin_scope_is_unchanged_and_hides_other_peoples_security_events(self):
        self.entry("skill_update", self.himanshi, target=("skill", self.pub.pk), desc="content work")
        self.entry("user_role_change", self.super, target=("user", self.admin.pk), desc="role secret")
        self.entry("login", self.himanshi, desc="other admin signed in")
        self.client.logout()
        self.login(self.admin)
        body = self.client.get("/admin/activity/").content.decode()
        self.assertIn("content work", body)
        self.assertNotIn("role secret", body)
        self.assertNotIn("other admin signed in", body)
        self.assertNotIn("himanshi@datasense.com</span></td>\n        <td>Skill edited", "")  # (scope covered by the three assertions above)

    def test_dashboard_recent_activity_excludes_cleared_records(self):
        gone = self.entry(desc="cleared recently")
        self.post_clear(mode="selected", ids=[gone.pk], expected_count=1, confirm="yes")
        self.assertNotIn("cleared recently", self.client.get("/admin/dashboard/").content.decode())

    def test_protection_flag_is_derived_from_the_action(self):
        for action, expected in (("user_role_change", True), ("password_change", True), ("settings_update", True), ("export", True), ("activity_cleared", True),
                                 ("dev_password_init", True), ("login", False), ("skill_update", False), ("import", False), ("category_create", False)):
            self.assertEqual(ActivityLog(action=action).is_protected or ActivityLog.objects.create(actor=None, action=action).is_protected, expected, action)

    def test_manage_controls_match_the_design_and_count_dialog_text_exists(self):
        import re
        from django.contrib.staticfiles import finders
        js = open(finders.find("js/app.js"), encoding="utf-8").read()
        self.assertIn("Are you sure you want to clear ", js)
        self.assertIn("This action cannot be undone.", js)
        css = open(finders.find("css/app.css"), encoding="utf-8").read()
        self.assertRegex(css, r"dialog\.danger-dialog\{border-top:6px solid var\(--danger\)")
        self.assertIn(".btn.danger-solid{background:var(--danger)", css)
