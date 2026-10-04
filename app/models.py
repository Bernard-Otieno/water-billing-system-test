import enum

from sqlalchemy import Column, Date, Enum, Float, ForeignKey, Integer, String, Table
from sqlalchemy.orm import relationship

from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    account_number = Column(String, unique=True, nullable=False)
    meter_number = Column(String, unique=True, nullable=False)
    address = Column(String, nullable=False)
    account_type = Column(String, nullable=False)  # e.g. "Residential"

    # this is how SQLAlchemy exposes the many-to-many link
    people = relationship(
        "Person", secondary="account_people", back_populates="accounts"
    )
    bills = relationship("Bill", back_populates="account")


class Person(Base):
    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone_number = Column(String, nullable=False)

    accounts = relationship(
        "Account", secondary="account_people", back_populates="people"
    )


# This is the junction table for the many-to-many link.
# It doesn't need its own model class since it holds no extra data (yet).

account_people = Table(
    "account_people",
    Base.metadata,
    Column("account_id", ForeignKey("accounts.id"), primary_key=True),
    Column("person_id", ForeignKey("people.id"), primary_key=True),
)


class Rate(Base):
    __tablename__ = "rates"

    id = Column(Integer, primary_key=True)
    account_type = Column(String, nullable=False)
    price_per_unit = Column(Float, nullable=False)
    effective_from = Column(Date, nullable=False)


class BillStatus(str, enum.Enum):
    unpaid = "unpaid"
    partial = "partial"
    paid = "paid"


class Bill(Base):
    __tablename__ = "bills"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    billing_month = Column(Integer, nullable=False)
    billing_year = Column(Integer, nullable=False)
    previous_reading = Column(Float, nullable=False)
    current_reading = Column(Float, nullable=False)
    units_used = Column(Float, nullable=False)
    rate_applied = Column(Float, nullable=False)  # frozen at time of billing
    amount_due = Column(Float, nullable=False)  # frozen at time of billing
    status = Column(Enum(BillStatus), default=BillStatus.unpaid, nullable=False)

    account = relationship("Account", back_populates="bills")
    payments = relationship("Payment", back_populates="bill")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey("bills.id"), nullable=False)
    amount = Column(Float, nullable=False)
    payment_date = Column(Date, nullable=False)

    bill = relationship("Bill", back_populates="payments")
