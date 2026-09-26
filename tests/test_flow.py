"""State and permission checks for the employee-to-reviewer vertical slice."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))


class WorkFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        os.environ["YOODUN_DATA_DIR"]=self.tmp.name
        import store
        import workflows
        self.store=store;self.workflows=workflows
        store.DATA=Path(self.tmp.name);store.DB=store.DATA/"work_os.sqlite3"
        project=Path(self.tmp.name)/"wuxiang";project.mkdir()
        store.PROJECT_ROOTS["wuxiang"]=project
        store.SHARED_ROOT=Path(self.tmp.name)/"shared";store.SHARED_ROOT.mkdir()
        store.initialize()
        self.founder=store.create_user("founder","Founder","founder","founder-password-test")
        self.manager=store.create_user("manager","Manager","manager","manager-password-test")
        self.employee=store.create_user("employee","Employee","employee","employee-password-test")
        self.other=store.create_user("other","Other","employee","other-password-test")
        (project/"character.png").write_bytes(b"character-baseline")
        (project/"motion.mp4").write_bytes(b"motion-reference")
        self.char=store.register_asset("wuxiang","character","定版老师",str(project/"character.png"),self.manager)
        self.motion=store.register_asset("wuxiang","motion_reference","真人动作",str(project/"motion.mp4"),self.manager)

    def tearDown(self):
        self.tmp.cleanup()

    def _task(self):
        s=self.store
        return s.create_task({"project_id":"wuxiang","workflow_id":"WF-01","title":"完成一招教学初版","why":"真实教学资产验收","input_assets":[self.char["id"],self.motion["id"]],"instructions":["核对角色","核对动作","提交结果"],"character_lock":{"asset_id":self.char["id"],"identity":"老师","version":1,"forbidden_changes":["衣服"]},"motion_lock":{"asset_id":self.motion["id"],"move":"起势","start":0,"end":4,"orientation":"正面","key_moments":["并步"],"version":1},"founder_required":True},self.manager)

    def test_realistic_revision_history_and_acceptance_gates(self):
        s=self.store;w=self.workflows
        task=self._task();tid=task["id"]
        with self.assertRaises(PermissionError):s.task_for_user(tid,{"id":self.other,"role":"employee"})
        with s.connect() as c:
            c.execute("UPDATE tasks SET ai_prepared=? WHERE id=?",(s.dumps({"context_summary":"真实任务","missing_inputs":[]}),tid))
            s.update_task_status(c,tid,{"planned"},"ready",self.manager)
        s.assign(tid,self.employee,self.manager)
        employee={"id":self.employee,"role":"employee"}
        manager={"id":self.manager,"role":"manager"}
        founder={"id":self.founder,"role":"founder"}
        s.start(tid,employee)
        first=s.register_submission_asset("wuxiang","first.md",b"version one",self.employee)
        v1=s.add_delivery(tid,employee,first["id"],None,None,"first",None)
        self.assertEqual(w.technical_qc(tid)["result"],"pass")
        with self.assertRaises(ValueError):w.human_review(tid,manager,"pass","looks fine",method="read",coverage="full")
        w.ai_skip(tid,manager,"text-only test")
        w.human_review(tid,manager,"fail","关键动作描述缺失","补动作顺序","保留角色",method="read",coverage="full")
        self.assertEqual(s.task_for_user(tid,employee)["status"],"revision_required")
        s.start(tid,employee)
        second=s.register_submission_asset("wuxiang","second.md",b"version two with motion",self.employee)
        v2=s.add_delivery(tid,employee,second["id"],None,None,"revision",None)
        self.assertEqual(v1["version"],1);self.assertEqual(v2["version"],2)
        w.technical_qc(tid)
        w.ai_skip(tid,manager,"text-only test")
        w.human_review(tid,manager,"pass","动作说明与参考顺序一致",method="逐项对照",coverage="整份文本")
        self.assertEqual(s.task_for_user(tid,employee)["status"],"human_review")
        w.founder_review(tid,founder,"pass","样板方向可用",method="人工审读",coverage="整份文本")
        result=s.detail(tid,employee)
        self.assertEqual(result["status"],"accepted")
        self.assertEqual([x["version"] for x in result["deliverables"]],[2,1])
        self.assertEqual(result["feedback"][0]["deliverable_id"],v1["id"])
        self.assertEqual(result["feedback"][0]["version"],1)

    def test_locked_asset_content_change_blocks_technical_qc(self):
        s=self.store;w=self.workflows
        task=self._task();tid=task["id"]
        with s.connect() as c:
            c.execute("UPDATE tasks SET ai_prepared=? WHERE id=?",(s.dumps({"missing_inputs":[]}),tid))
            s.update_task_status(c,tid,{"planned"},"ready",self.manager)
        s.assign(tid,self.employee,self.manager)
        employee={"id":self.employee,"role":"employee"}
        s.start(tid,employee)
        output=s.register_submission_asset("wuxiang","out.md",b"content",self.employee)
        s.add_delivery(tid,employee,output["id"],None,None,"",None)
        Path(self.char["storage_ref"]).write_bytes(b"changed")
        qc=w.technical_qc(tid)
        self.assertEqual(qc["result"],"fail")
        self.assertEqual(s.task_for_user(tid,employee)["status"],"revision_required")
        self.assertTrue(s.detail(tid,employee)["feedback"])

    def test_secrets_and_cross_project_rejected(self):
        s=self.store
        root=s.PROJECT_ROOTS["wuxiang"]
        (root/"auth.json").write_text("secret")
        with self.assertRaises(ValueError):s.register_asset("wuxiang","document","credential",str(root/"auth.json"),self.manager)
        outside=Path(self.tmp.name)/"outside.md";outside.write_text("outside")
        with self.assertRaises(ValueError):s.register_asset("wuxiang","document","outside",str(outside),self.manager)

    def test_remote_company_workflow_result_is_idempotent(self):
        import adapters
        s=self.store
        task=self._task();tid=task["id"]
        os.environ["YOODUN_CONNECTOR_MODE"]="remote"
        try:
            queued=adapters.submit_ai_job(tid,"prepare",self.manager)
            self.assertEqual(queued["status"],"queued")
            claimed=adapters.connector_claim(queued["id"])
            self.assertEqual(claimed["spec"]["request_key"],queued["request_key"])
            prepared={"context_summary":"source bound","teaching_structure":[],"script":"","shot_plan":[],"generation_prompt":"","negative_constraints":[],"reference_mapping":[],"qc_checklist":[],"missing_inputs":[]}
            report={"status":"complete","plan_id":"plan-test","final_job":"job-test","body":prepared,"usage":{"input_tokens":10,"output_tokens":5}}
            self.assertEqual(adapters.connector_report(queued["id"],report)["status"],"complete")
            self.assertEqual(adapters.connector_report(queued["id"],report)["status"],"complete")
            self.assertEqual(s.task_for_user(tid,{"id":self.manager,"role":"manager"})["status"],"ready")
            with s.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM costs WHERE task_id=?",(tid,)).fetchone()[0],1)
        finally:os.environ.pop("YOODUN_CONNECTOR_MODE",None)

    def test_candidate_choice_and_task_copilot_are_scoped(self):
        import adapters
        s=self.store
        tid=self._task()["id"]
        with s.connect() as c:
            c.execute("UPDATE tasks SET ai_prepared=? WHERE id=?",(s.dumps({"context_summary":"current task","missing_inputs":[]}),tid))
            s.update_task_status(c,tid,{"planned"},"ready",self.manager)
        s.assign(tid,self.employee,self.manager)
        employee={"id":self.employee,"role":"employee"}
        other={"id":self.other,"role":"employee"}
        s.start(tid,employee)
        first=s.add_candidate(tid,employee,{"label":"版本 A","external_url":"https://example.com/a","note":"动作不稳"})
        second=s.add_candidate(tid,employee,{"label":"版本 B","external_url":"https://example.com/b","note":"动作清楚"})
        with self.assertRaises(PermissionError):s.choose_candidate(tid,first["id"],"选择 A",other)
        s.choose_candidate(tid,second["id"],"角色和动作都更清楚",employee)
        self.assertEqual(s.selected_candidate(tid,employee)["id"],second["id"])
        os.environ["YOODUN_CONNECTOR_MODE"]="remote"
        try:
            message=adapters.submit_copilot(tid,employee,"steps","真人动作应该核对哪一段？")
            with s.connect() as c:
                job=dict(c.execute("SELECT * FROM ai_jobs WHERE id=(SELECT ai_job_id FROM copilot_messages WHERE id=?)",(message["id"],)).fetchone())
            claim=adapters.connector_claim(job["id"])
            self.assertIn("真人动作应该核对哪一段",claim["spec"]["units"][0]["brief"])
            report={"status":"complete","plan_id":"plan-copilot","final_job":"job-copilot","body":{"answer":"核对锁定参考的 0–4 秒。","next_action":"观看该区间并记录起止姿态。","needs_manager":False},"usage":{"prompt_tokens":40,"completion_tokens":20}}
            self.assertEqual(adapters.connector_report(job["id"],report)["status"],"complete")
            self.assertEqual(adapters.connector_report(job["id"],report)["status"],"complete")
            detail=s.detail(tid,employee)
            self.assertEqual(detail["status"],"in_progress")
            self.assertNotIn("budget_cap",detail)
            self.assertNotIn("storage_ref",detail["assets"][0])
            self.assertEqual(detail["copilot_messages"][0]["status"],"complete")
            self.assertEqual(detail["candidates"][0]["selected"],1)
            second_message=adapters.submit_copilot(tid,employee,"budget","可以增加付费生成吗？")
            with s.connect() as c:
                second_job=c.execute("SELECT ai_job_id FROM copilot_messages WHERE id=?",(second_message["id"],)).fetchone()[0]
            adapters.connector_claim(second_job)
            adapters.connector_report(second_job,{"status":"failed","error":"provider unavailable"})
            unresolved=s.detail(tid,employee)
            self.assertEqual(unresolved["status"],"in_progress")
            self.assertTrue(unresolved["escalations"])
            self.assertEqual(unresolved["escalations"][0]["status"],"open")
            s.resolve_escalation(tid,unresolved["escalations"][0]["id"],"负责人核对报价后另行决定",{"id":self.manager,"role":"manager"})
            self.assertEqual(s.detail(tid,employee)["escalations"][0]["status"],"resolved")
        finally:os.environ.pop("YOODUN_CONNECTOR_MODE",None)


if __name__=="__main__":unittest.main()
