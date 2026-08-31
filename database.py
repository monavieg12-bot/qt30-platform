from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./platform.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    role = Column(String(20), default="expert") # "client" 或 "expert"
    points = Column(Integer, default=100)        # 預設贈送 100 點
    is_verified = Column(Boolean, default=False) # 牌照審核狀態
    license_url = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    unlocks = relationship("LeadUnlock", back_populates="expert")

class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    district = Column(String(50), nullable=False)
    budget = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(255), nullable=True)

    # 發案人真實聯絡資訊（未解鎖前保護）
    client_name = Column(String(50), nullable=False)
    client_phone = Column(String(20), nullable=False)
    client_line = Column(String(50), nullable=True)

    # 媒合名單搶單參數
    unlock_fee = Column(Integer, default=30)     # 解鎖單筆名單扣除點數
    max_unlocks = Column(Integer, default=3)     # 限制最多 3 位師傅搶單
    current_unlocks = Column(Integer, default=0) # 已解鎖人數
    created_at = Column(DateTime, default=datetime.utcnow)

    unlocks = relationship("LeadUnlock", back_populates="case")

class LeadUnlock(Base):
    __tablename__ = "lead_unlocks"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    expert_id = Column(Integer, ForeignKey("users.id"))
    unlocked_at = Column(DateTime, default=datetime.utcnow)

    case = relationship("Case", back_populates="unlocks")
    expert = relationship("User", back_populates="unlocks")

def init_db():
    Base.metadata.create_all(bind=engine)