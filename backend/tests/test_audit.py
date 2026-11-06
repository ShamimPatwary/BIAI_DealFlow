import unittest
import os
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import Base, Session, Lead, Activity, app, audit, duplicate_groups_for, engine, norm_company, query_result, seed
from sqlalchemy import delete, select


class DealFlowAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        seed()
        with Session(engine) as db:
            cls.leads = list(db.scalars(select(Lead).where(Lead.archived.is_(False))))

    def test_normalized_company_suffix(self):
        self.assertEqual(norm_company("  ABC   Limited. "), "abc")

    def test_actual_duplicate_groups(self):
        pairs = {tuple(sorted(lead.id for lead in group)) for _, group, _ in duplicate_groups_for(self.leads)}
        self.assertEqual(pairs, {("L01", "L23"), ("L02", "L03")})

    def test_sample_audit_flags_nine_original_rows(self):
        findings = audit()
        flagged = {row["lead_id"] for row in findings}
        self.assertEqual(len(self.leads), 25)
        self.assertEqual(len(flagged), 9)
        self.assertTrue(any(row["lead_id"] == "L25" and row["type"] == "Date order" for row in findings))
        self.assertTrue(any(row["lead_id"] == "L12" and row["type"] == "Stage/notes mismatch" for row in findings))

    def test_proposal_sum_uses_actual_rows(self):
        result = query_result("What is the total value in Proposal stage?")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["value"], 1_620_000)

    def test_overdue_query_excludes_terminal_stages(self):
        result = query_result("Which deals are overdue?")
        stages = {row["stage"] for row in result["leads"]}
        self.assertNotIn("Paid", stages)
        self.assertNotIn("Lost", stages)

    def test_api_lead_edit_and_note_based_draft(self):
        os.environ["AI_PROVIDER"]="demo"
        client=TestClient(app)
        # The list endpoint and app edit endpoint are exercised without leaving sample edits behind.
        before=next(x for x in client.get("/api/v1/leads").json() if x["id"]=="L10")
        with Session(engine) as db:
            activity_before={x.id for x in db.scalars(select(Activity.id).where(Activity.lead_id=="L10"))}
        try:
            response=client.patch("/api/v1/leads/L10",json={"owner":"Test owner","stage":"Proposal"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()["owner"],"Test owner")
            self.assertEqual(response.json()["stage"],"Proposal")
            draft=client.post("/api/v1/ai/follow-up-email",json={"lead_id":"L10"}).json()
            self.assertIn("meeting done",draft["body"].lower())
            self.assertEqual(draft["mode"],"Demo template")
        finally:
            client.patch("/api/v1/leads/L10",json={"owner":before["owner"],"stage":before["stage"]})
            with Session(engine) as db:
                db.execute(delete(Activity).where(Activity.lead_id=="L10",Activity.id.not_in(activity_before)))
                db.commit()

    def test_home_and_csv_exports(self):
        client=TestClient(app)
        self.assertEqual(client.get("/").status_code,200)
        self.assertIn("BIAI DealFlow",client.get("/").text)
        self.assertIn("text/csv",client.get("/api/v1/export/csv").headers["content-type"])
        csv_path=Path(__file__).parents[2]/"sample-data"/"intern"/"task2-corporate-leads"/"corporate-leads.csv"
        with csv_path.open("rb") as data:
            preview=client.post("/api/v1/import/csv",files={"file":("sample.csv",data,"text/csv")})
        self.assertEqual(preview.status_code,200)
        self.assertEqual(preview.json()["rows"],25)


if __name__ == "__main__":
    unittest.main()
