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

class ResetFlowTest(unittest.TestCase):
    def setUp(self):
        fd,self.path=tempfile.mkstemp(suffix=".db"); os.close(fd); self.db=ContinuityDB(self.path)
        self.producer=self.db.add_user("制片","producer"); self.continuity=self.db.add_user("场记","continuity"); self.reviewer=self.db.add_user("审片","reviewer")
        self.production=self.db.create_production("测试影片","可逆变化",self.producer)
        self.scene=self.db.add_scene(self.production,"S01","换装",1)
        self.s1=self.db.add_shot(self.scene,"S01-01",1,1,"出场",self.continuity)
        self.s2=self.db.add_shot(self.scene,"S01-02",2,2,"弄脏",self.continuity)
        self.s3=self.db.add_shot(self.scene,"S01-03",3,3,"修复后",self.continuity)
        self.costume=self.db.add_element(self.production,"主角外套污损","costume","monotonic","污损只能加重")
        self.db.set_element_state(self.s1,self.costume,"轻度",1,"",self.continuity)
        self.db.set_element_state(self.s2,self.costume,"重度",3,"",self.continuity)
        self.db.set_element_state(self.s3,self.costume,"中度",2,"",self.continuity)
    def tearDown(self): self.db.close(); os.unlink(self.path)
    def test_reset_review_creates_new_baseline(self):
        conflicts=self.db.check_scene(self.scene)
        self.assertEqual(1,len(conflicts)); self.assertEqual("regression",conflicts[0]["kind"])
        reset=self.db.propose_reset(self.costume,self.s3,"轻度",1,"服装修复后污损减轻","剧本第12场注明外套送洗",self.continuity)
        with self.assertRaisesRegex(DomainError,"待审核"):
            self.db.propose_reset(self.costume,self.s3,"轻度",1,"重复申请","重复依据",self.continuity)
        with self.assertRaisesRegex(DomainError,"只有审片人"):
            self.db.review_reset(reset,True,self.continuity)
        result=self.db.review_reset(reset,True,self.reviewer,"同意")
        self.assertEqual("approved",result["status"]); self.assertEqual([],result["conflicts"])
        row=self.db.conn.execute("SELECT * FROM reset_requests WHERE id=?", (reset,)).fetchone()
        self.assertEqual("中度",row["prev_value"]); self.assertEqual(2,row["prev_numeric"])
        state=self.db.conn.execute("SELECT * FROM element_states WHERE shot_id=? AND element_id=?", (self.s3,self.costume)).fetchone()
        self.assertEqual(1,state["numeric_value"])
        s4=self.db.add_shot(self.scene,"S01-04",4,4,"后续",self.continuity)
        self.db.set_element_state(s4,self.costume,"完好",0,"",self.continuity)
        conflicts=self.db.check_scene(self.scene)
        self.assertEqual(1,len(conflicts)); self.assertEqual("regression",conflicts[0]["kind"])
        self.assertEqual(self.s3,conflicts[0]["from_shot_id"])
    def test_locked_shot_cannot_bypass_reset(self):
        self.db.check_scene(self.scene)
        reset=self.db.propose_reset(self.costume,self.s3,"轻度",1,"服装修复","服装间记录第7条",self.continuity)
        self.db.review_reset(reset,True,self.reviewer)
        for shot in (self.s1,self.s2,self.s3): self.db.lock_shot(shot,self.continuity)
        again=self.db.propose_reset(self.costume,self.s3,"完好",0,"再次复位","导演确认补拍",self.continuity)
        self.db.review_reset(again,True,self.reviewer)
        state=self.db.conn.execute("SELECT * FROM element_states WHERE shot_id=? AND element_id=?", (self.s3,self.costume)).fetchone()
        self.assertEqual(0,state["numeric_value"])
        with self.assertRaisesRegex(DomainError,"锁定"):
            self.db.set_element_state(self.s3,self.costume,"轻度",1,"直接改",self.continuity)
    def test_reset_validation(self):
        stable=self.db.add_element(self.production,"发型","character","stable","")
        with self.assertRaisesRegex(DomainError,"单调规则"):
            self.db.propose_reset(stable,self.s3,"盘发",1,"原因","依据",self.continuity)
        with self.assertRaisesRegex(DomainError,"原因和依据"):
            self.db.propose_reset(self.costume,self.s3,"轻度",1,"","",self.continuity)
        with self.assertRaisesRegex(DomainError,"不属于同一项目"):
            self.db.propose_reset(self.costume,999,"轻度",1,"原因","依据",self.continuity)
        reset=self.db.propose_reset(self.costume,self.s3,"轻度",1,"服装修复","服装间记录",self.continuity)
        result=self.db.review_reset(reset,False,self.reviewer,"依据不足")
        self.assertEqual("rejected",result["status"])
        conflicts=self.db.check_scene(self.scene)
        self.assertEqual(1,len(conflicts))

if __name__=="__main__": unittest.main()
