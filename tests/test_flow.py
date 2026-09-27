import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import ContinuityDB, DomainError

class ContinuityFlowTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=ContinuityDB(self.path)
        self.producer=self.db.add_user("制片","producer"); self.continuity=self.db.add_user("场记","continuity"); self.reviewer=self.db.add_user("审片","reviewer")
        self.production=self.db.create_production("测试影片","非线性拍摄",self.producer)
        self.scene=self.db.add_scene(self.production,"S01","雨夜",1)
        self.s1=self.db.add_shot(self.scene,"S01-01",2,1,"受伤后",self.continuity)
        self.s2=self.db.add_shot(self.scene,"S01-02",1,2,"受伤前",self.continuity)
        self.injury=self.db.add_element(self.production,"手臂伤痕","injury","monotonic","只能加重")
        self.db.set_element_state(self.s1,self.injury,"重度",3,"",self.continuity)
        self.db.set_element_state(self.s2,self.injury,"轻度",1,"",self.continuity)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def test_conflict_plan_review_and_lock_flow(self):
        conflicts=self.db.check_scene(self.scene)
        self.assertEqual("regression",conflicts[0]["kind"])
        plan=self.db.propose_adjustment(conflicts[0]["id"],"重度",3,"将伤势调整到叙事顺序上的中间状态",self.continuity)
        result=self.db.review_adjustment(plan,True,self.reviewer,"通过")
        self.assertEqual([],result["conflicts"])
        self.db.lock_shot(self.s1,self.continuity); self.db.lock_shot(self.s2,self.continuity)
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.set_element_state(self.s2,self.injury,"重度",3,"重做",self.continuity)
    def test_exemption_and_validation_failures(self):
        conflicts=self.db.check_scene(self.scene)
        with self.assertRaisesRegex(DomainError,"单调规则"):
            self.db.set_element_state(self.s1,self.injury,"未知",None,"",self.continuity)
        self.db.approve_exemption(conflicts[0]["id"],"闪回镜头中伤痕表现属于刻意叙事误差",self.reviewer)
        self.db.lock_shot(self.s1,self.continuity); self.db.lock_shot(self.s2,self.continuity)
        with self.assertRaisesRegex(DomainError,"只有审片人"):
            self.db.review_adjustment(999,True,self.continuity,"无权审核")

    def test_reversible_costume_reset_sets_new_baseline_and_archives_state(self):
        self.db.set_element_state(self.s2,self.injury,"重度",3,"",self.continuity)
        coat=self.db.add_element(self.production,"主角风衣","costume","monotonic","湿污程度，可复位")
        self.db.set_element_state(self.s1,coat,"湿透",3,"",self.continuity)
        self.db.set_element_state(self.s2,coat,"湿透",3,"清洗前登记",self.continuity)
        self.assertEqual([],[c for c in self.db.check_scene(self.scene) if c["element_id"]==coat])

        reset=self.db.propose_reset(coat,self.s2,"已清洁",0,"戏服完成清洁后回到当前叙事基准","服装组交接单第7场",self.continuity)
        with self.assertRaisesRegex(DomainError,"未处理"):
            self.db.propose_reset(coat,self.s2,"已清洁",0,"重复申请","重复依据",self.producer)
        with self.assertRaisesRegex(DomainError,"只有审片人"):
            self.db.review_reset(reset,True,self.continuity,"场记不能审核")
        result=self.db.review_reset(reset,True,self.reviewer,"画面与道具单一致")
        self.assertEqual([],[c for c in result["conflicts"] if c["status"]!="exempted"])
        state=self.db.conn.execute("SELECT * FROM element_states WHERE shot_id=? AND element_id=?",(self.s2,coat)).fetchone()
        self.assertEqual(0,state["numeric_value"])
        record=self.db.conn.execute("SELECT * FROM element_resets WHERE id=?",(reset,)).fetchone()
        self.assertEqual(3,record["previous_numeric_value"])
        self.assertEqual("湿透",record["previous_state_value"])
        self.db.set_element_state(self.s2,coat,"已清洁",0,"未重新登记的再次回退",self.continuity)
        coat_conflicts=[c for c in self.db.check_scene(self.scene) if c["element_id"]==coat and c["kind"]=="regression"]
        self.assertEqual("regression",coat_conflicts[0]["kind"])
        with self.assertRaisesRegex(DomainError,"复位申请不存在或已处理"):
            self.db.review_reset(reset,True,self.reviewer,"不能重复确认")

        s3=self.db.add_shot(self.scene,"S01-03",3,3,"复位后的后续镜头",self.continuity)
        s4=self.db.add_shot(self.scene,"S01-04",4,4,"普通回退检查",self.continuity)

        self.db.set_element_state(s3,coat,"沾水",1,"复位后的普通状态",self.continuity)
        later_conflicts=[c for c in self.db.check_scene(self.scene) if c["to_shot_id"]==s3 and c["status"]!="exempted"]
        self.assertEqual([],later_conflicts)
        self.db.set_element_state(s4,coat,"干净",0,"未登记复位的普通回退",self.continuity)
        conflicts=[c for c in self.db.check_scene(self.scene) if c["element_id"]==coat and c["kind"]=="regression"]
        self.assertEqual("regression",conflicts[0]["kind"])

    def test_locked_shot_allows_reviewed_reset_but_not_direct_change(self):
        injury_conflict=self.db.check_scene(self.scene)[0]
        self.db.approve_exemption(injury_conflict["id"],"基础示例中的伤痕闪回保留",self.reviewer)
        prop=self.db.add_element(self.production,"血迹怀表","prop","monotonic","污损程度，道具清洗后可复位")
        self.db.set_element_state(self.s1,prop,"带血",4,"",self.continuity)
        self.db.set_element_state(self.s2,prop,"清洁",0,"",self.continuity)
        conflict=[c for c in self.db.check_scene(self.scene) if c["element_id"]==prop][0]
        self.db.approve_exemption(conflict["id"],"复位流程上线前的临时豁免",self.reviewer)
        self.db.lock_shot(self.s1,self.continuity); self.db.lock_shot(self.s2,self.continuity)
        shot=self.db.conn.execute("SELECT * FROM shots WHERE id=?",(self.s2,)).fetchone()
        self.assertEqual("locked",shot["status"])
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.set_element_state(self.s2,prop,"清洁",0,"直接改锁定镜头",self.continuity)

        reset=self.db.propose_reset(prop,self.s2,"已清洗",0,"道具组清洗后复位","道具复位登记表",self.continuity)
        result=self.db.review_reset(reset,True,self.reviewer,"确认登记依据")
        self.assertEqual([],[c for c in result["conflicts"] if c["status"]!="exempted"])
        shot=self.db.conn.execute("SELECT * FROM shots WHERE id=?",(self.s2,)).fetchone()
        self.assertEqual("locked",shot["status"])
        state=self.db.conn.execute("SELECT * FROM element_states WHERE shot_id=? AND element_id=?",(self.s2,prop)).fetchone()
        self.assertEqual(0,state["numeric_value"])

    def test_approved_reset_rechecks_cross_scene_narrative_chain(self):
        self.db.set_element_state(self.s2,self.injury,"重度",3,"",self.continuity)
        scene2=self.db.add_scene(self.production,"S02","清晨",2)
        t1=self.db.add_shot(scene2,"S02-01",1,1,"服装复位后的镜头",self.continuity)
        t2=self.db.add_shot(scene2,"S02-02",2,2,"后续状态",self.continuity)
        t3=self.db.add_shot(scene2,"S02-03",3,3,"普通回退检查",self.continuity)
        coat=self.db.add_element(self.production,"跨场外套","costume","monotonic","可清洗")
        self.db.set_element_state(self.s1,coat,"湿透",3,"",self.continuity)
        self.db.set_element_state(t1,coat,"湿透",3,"清洗前",self.continuity)
        reset=self.db.propose_reset(coat,t1,"已清洁",0,"跨场拍摄前完成服装清洗","服装清洗交接单",self.continuity)
        result=self.db.review_reset(reset,True,self.reviewer,"通过")
        self.assertEqual({self.scene,scene2},set(result["scene_ids"]))
        self.assertEqual([],self.db.list_conflicts(scene2))
        self.db.set_element_state(t2,coat,"沾水",1,"",self.continuity)
        self.assertEqual([],self.db.check_scene(scene2))
        self.db.set_element_state(t3,coat,"干净",0,"未登记复位",self.continuity)
        self.assertEqual("regression",self.db.check_scene(scene2)[0]["kind"])

    def test_rejected_reset_releases_pending_slot(self):
        coat=self.db.add_element(self.production,"备用外套","costume","monotonic","可清洗")
        reset=self.db.propose_reset(coat,self.s2,"已清洁",0,"第一次申请","第一次依据",self.continuity)
        rejected=self.db.review_reset(reset,False,self.reviewer,"依据不完整")
        self.assertEqual("rejected",rejected["status"])
        second=self.db.propose_reset(coat,self.s2,"已清洁",0,"补正后的申请","补正依据",self.continuity)
        self.assertEqual("approved",self.db.review_reset(second,True,self.reviewer,"通过")["status"])

if __name__=="__main__": unittest.main()
