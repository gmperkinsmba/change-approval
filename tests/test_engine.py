import hashlib
import sqlite3
import threading
import time
import tempfile
import unittest

from rba import PermissionDenied, ReviewEngine
from rba.engine import ReviewError


class Clock:
    def __init__(self): self.value = 1_800_000_000.0
    def __call__(self): return self.value


class ReviewEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.engine = ReviewEngine(self.temp.name, clock=self.clock)

    def tearDown(self): self.temp.cleanup()

    def plan(self, payload="Reviewed document text.", target="DOC-001"):
        return self.engine.prepare(target, payload)

    def approve(self, plan):
        return self.engine.approve(plan.plan_id, plan.version, plan.fingerprint)

    def test_happy_path_binds_review_to_exact_change_and_reads_back(self):
        plan = self.plan()
        approval = self.approve(plan)
        receipt = self.engine.execute(approval.approval_id)
        self.assertEqual(receipt.status, "verified")
        self.assertEqual(receipt.tool_status, "success")
        self.assertEqual(receipt.target_id, "DOC-001")
        self.assertEqual(receipt.observed_revision, "5")
        self.assertEqual(receipt.observed_fingerprint, hashlib.sha256(b"Reviewed document text.").hexdigest())

    def test_payload_change_invalidates_old_approval_until_new_review(self):
        plan = self.plan("First reviewed text")
        old_approval = self.approve(plan)
        changed = self.engine.revise(plan.plan_id, plan.version, plan.target_id, "Changed after review")
        self.assertNotEqual(plan.fingerprint, changed.fingerprint)
        rejected = self.engine.execute(old_approval.approval_id)
        self.assertEqual(rejected.status, "rejected")
        self.assertIn("fingerprint changed", rejected.detail)
        self.assertEqual(self.engine.adapter.get(plan.target_id)["revision"], plan.expected_revision)
        new_approval = self.approve(changed)
        verified = self.engine.execute(new_approval.approval_id)
        self.assertEqual(verified.status, "verified")
        self.assertEqual(self.engine.adapter.get(plan.target_id)["content"], "Changed after review")

    def test_target_change_invalidates_old_approval(self):
        plan = self.plan("Change target text", "DOC-001")
        approval = self.approve(plan)
        changed = self.engine.revise(plan.plan_id, plan.version, "DOC-002", "Change target text")
        self.assertEqual(changed.target_id, "DOC-002")
        self.assertNotEqual(changed.fingerprint, plan.fingerprint)
        self.assertEqual(self.engine.execute(approval.approval_id).status, "rejected")
        self.assertEqual(self.engine.adapter.get("DOC-002")["revision"], "2")

    def test_destination_revision_change_rejects_action(self):
        plan = self.plan()
        approval = self.approve(plan)
        self.engine.adapter.apply(plan.target_id, plan.expected_revision, plan.expected_before, "External local change")
        receipt = self.engine.execute(approval.approval_id)
        self.assertEqual(receipt.status, "rejected")
        self.assertIn("Destination changed", receipt.detail)
        self.assertEqual(self.engine.adapter.get(plan.target_id)["content"], "External local change")

    def test_destination_change_before_approval_requires_repreparation(self):
        plan=self.plan()
        self.engine.adapter.apply(plan.target_id,plan.expected_revision,plan.expected_before,"External before approval")
        with self.assertRaisesRegex(ReviewError,"changed after preparation"):
            self.approve(plan)

    def test_expired_approval_cannot_apply(self):
        plan = self.plan()
        approval = self.approve(plan)
        self.clock.value = approval.expires_at + 0.1
        receipt = self.engine.execute(approval.approval_id)
        self.assertEqual(receipt.status, "rejected")
        self.assertIn("expired", receipt.detail)
        self.assertEqual(self.engine.adapter.get(plan.target_id)["revision"], plan.expected_revision)

    def test_success_without_readback_is_unverified_until_new_readback_receipt(self):
        plan = self.plan()
        approval = self.approve(plan)
        unverified = self.engine.execute(approval.approval_id, readback=False)
        self.assertEqual(unverified.tool_status, "success")
        self.assertEqual(unverified.status, "unverified")
        self.assertIn("readback was not run", unverified.detail)
        verified = self.engine.verify(unverified.receipt_id)
        self.assertEqual(verified.status, "verified")
        self.assertEqual(verified.parent_receipt_id, unverified.receipt_id)
        with sqlite3.connect(self.engine.db_path) as db:
            original = db.execute("SELECT status FROM action_receipts WHERE receipt_id=?", (unverified.receipt_id,)).fetchone()[0]
        self.assertEqual(original, "unverified")

    def test_readback_mismatch_remains_unverified_after_tool_success(self):
        plan = self.plan("Reviewed content")
        approval = self.approve(plan)
        unverified = self.engine.execute(approval.approval_id, readback=False)
        self.engine.adapter.apply(plan.target_id, "5", "Reviewed content", "Changed before readback")
        check = self.engine.verify(unverified.receipt_id)
        self.assertEqual(check.tool_status, "success")
        self.assertEqual(check.status, "unverified")
        self.assertIn("does not match", check.detail)

    def test_execute_permission_is_checked_inside_engine(self):
        plan = self.plan()
        approval = self.approve(plan)
        self.engine.update_settings({"can_execute": False})
        with self.assertRaisesRegex(PermissionDenied, "execute"):
            self.engine.execute(approval.approval_id)
        self.assertEqual(self.engine.adapter.get(plan.target_id)["revision"], plan.expected_revision)

    def test_readback_permission_is_required_for_verified_execute(self):
        plan=self.plan();approval=self.approve(plan)
        self.engine.update_settings({"can_verify":False})
        with self.assertRaisesRegex(PermissionDenied,"verify"):
            self.engine.execute(approval.approval_id,readback=True)
        self.assertEqual(self.engine.adapter.get(plan.target_id)["revision"],plan.expected_revision)
        receipt=self.engine.execute(approval.approval_id,readback=False)
        self.assertEqual(receipt.status,"unverified")

    def test_revision_waits_for_execution_lock(self):
        plan=self.plan();approval=self.approve(plan)
        entered=threading.Event();release=threading.Event();revision_done=threading.Event();result={}
        original=self.engine.adapter.apply
        def slow_apply(*args):
            entered.set()
            if not release.wait(3):raise TimeoutError("test gate timed out")
            return original(*args)
        self.engine.adapter.apply=slow_apply
        action=threading.Thread(target=lambda:result.setdefault("receipt",self.engine.execute(approval.approval_id)))
        action.start();self.assertTrue(entered.wait(2))
        revision=threading.Thread(target=lambda:(result.setdefault("plan",self.engine.revise(plan.plan_id,1,"DOC-001","Revised while action waits")),revision_done.set()))
        revision.start();time.sleep(.08)
        self.assertFalse(revision_done.is_set())
        release.set();action.join(3);revision.join(3)
        self.assertFalse(action.is_alive());self.assertFalse(revision.is_alive())
        self.assertEqual(result["receipt"].status,"verified")
        self.assertEqual(result["plan"].version,2)

    def test_permission_revocation_waits_for_execution_lock(self):
        plan=self.plan();approval=self.approve(plan)
        entered=threading.Event();release=threading.Event();settings_done=threading.Event();result={}
        original=self.engine.adapter.apply
        def slow_apply(*args):
            entered.set()
            if not release.wait(3):raise TimeoutError("test gate timed out")
            return original(*args)
        self.engine.adapter.apply=slow_apply
        action=threading.Thread(target=lambda:result.setdefault("receipt",self.engine.execute(approval.approval_id)))
        action.start();self.assertTrue(entered.wait(2))
        settings=threading.Thread(target=lambda:(result.setdefault("settings",self.engine.update_settings({"can_execute":False})),settings_done.set()))
        settings.start();time.sleep(.08)
        self.assertFalse(settings_done.is_set())
        release.set();action.join(3);settings.join(3)
        self.assertEqual(result["receipt"].status,"verified")
        self.assertFalse(result["settings"]["can_execute"])
        with self.assertRaisesRegex(PermissionDenied,"execute"):
            self.engine.execute(approval.approval_id)

    def test_approval_and_plan_version_rows_are_immutable(self):
        plan = self.plan()
        approval = self.approve(plan)
        with sqlite3.connect(self.engine.db_path) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("UPDATE approval_receipts SET actor='changed' WHERE approval_id=?", (approval.approval_id,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                db.execute("DELETE FROM plan_versions WHERE plan_id=?", (plan.plan_id,))

    def test_unknown_targets_oversize_and_invalid_settings_reject(self):
        with self.assertRaisesRegex(ReviewError, "local synthetic"):
            self.engine.prepare("https://outside.invalid/doc", "text")
        with self.assertRaisesRegex(ReviewError, "64 KB"):
            self.engine.prepare("DOC-001", "x" * 64_001)
        with self.assertRaisesRegex(ReviewError, "between 30 and 3600"):
            self.engine.update_settings({"approval_ttl_seconds": 1})


if __name__ == "__main__": unittest.main()
