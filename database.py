import json
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./platform.db"

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ExpertModel(Base):
    __tablename__ = "experts"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    rating = Column(Float, default=4.9)
    wallet_points = Column(Integer, default=5000)
    dispute_rate = Column(Float, default=0.01)
    is_licensed = Column(Boolean, default=False)  # 審核通過後才為 True
    license_number = Column(String, nullable=True)  # 裝修牌照/證照字號
    license_file_url = Column(String, nullable=True)  # 證照照片路徑
    verification_status = Column(String, default="unverified")  # unverified / pending / approved / rejected

class DemandModel(Base):
    __tablename__ = "demands"

    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False)
    budget = Column(Integer, nullable=False)
    region = Column(String, nullable=False)
    detailed_address = Column(String, nullable=False)
    has_supervision = Column(Boolean, default=False)
    supervision_fee = Column(Integer, default=0)
    total_contract_amount = Column(Integer, default=0)
    base_lead_price = Column(Integer, default=300)
    _unlocked_experts = Column(Text, default="[]")
    _photos = Column(Text, default="[]")

    @property
    def unlocked_experts(self):
        return json.loads(self._unlocked_experts) if self._unlocked_experts else []

    @unlocked_experts.setter
    def unlocked_experts(self, value):
        self._unlocked_experts = json.dumps(value)

    @property
    def photos(self):
        return json.loads(self._photos) if self._photos else []

    @photos.setter
    def photos(self, value):
        self._photos = json.dumps(value)

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    expert = db.query(ExpertModel).filter(ExpertModel.id == "exp_1").first()
    if not expert:
        default_expert = ExpertModel(
            id="exp_1",
            name="金牌水電行-阿銘",
            rating=4.9,
            wallet_points=5000,
            dispute_rate=0.01,
            is_licensed=False,
            verification_status="unverified"
        )
        db.add(default_expert)
        db.commit()
    db.close()