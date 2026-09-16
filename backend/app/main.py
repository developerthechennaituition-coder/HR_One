from datetime import datetime, timezone

from bson import ObjectId
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.db.mongodb import client, db, ensure_companies_collection, MONGODB_DATABASE


app = FastAPI(title="HRMS Backend")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


companies_collection = db["companies"]


@app.on_event("startup")
def initialize_database():
    ensure_companies_collection()


class CompanyPayload(BaseModel):
    company: str = Field(min_length=1)
    legal_entity: str = Field(
        min_length=1,
        validation_alias=AliasChoices("legal_entity", "legalEntity"),
    )
    branch: str = Field(min_length=1)
    location: str = Field(min_length=1)

    @field_validator("company", "legal_entity", "branch", "location")
    @classmethod
    def strip_and_validate(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CompanyResponse(CompanyPayload):
    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


def serialize_company(company: dict) -> dict:
    company["id"] = str(company.pop("_id"))
    return company


def parse_company_id(company_id: str) -> ObjectId:
    if not ObjectId.is_valid(company_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company id",
        )
    return ObjectId(company_id)


def get_active_company(company_id: str) -> dict:
    company = companies_collection.find_one(
        {"_id": parse_company_id(company_id), "is_active": True}
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )
    return serialize_company(company)


@app.post(
    "/api/companies",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_company(payload: CompanyPayload):
    now = datetime.now(timezone.utc)
    company = {
        **payload.model_dump(),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    result = companies_collection.insert_one(company)
    company["_id"] = result.inserted_id
    return serialize_company(company)


@app.get("/api/companies", response_model=list[CompanyResponse])
def list_companies():
    companies = companies_collection.find({"is_active": True}).sort(
        "created_at", -1
    )
    return [serialize_company(company) for company in companies]


@app.get("/api/companies/{company_id}", response_model=CompanyResponse)
def get_company(company_id: str):
    return get_active_company(company_id)


@app.put("/api/companies/{company_id}", response_model=CompanyResponse)
def update_company(company_id: str, payload: CompanyPayload):
    object_id = parse_company_id(company_id)
    now = datetime.now(timezone.utc)
    result = companies_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {**payload.model_dump(), "updated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )
    return get_active_company(company_id)


@app.delete(
    "/api/companies/{company_id}",
    summary="Deactivate a company",
    description=(
        "Marks the company inactive. The document is retained; permanent deletion "
        "policy remains TBD."
    ),
)
def deactivate_company(company_id: str):
    object_id = parse_company_id(company_id)
    now = datetime.now(timezone.utc)
    result = companies_collection.update_one(
        {"_id": object_id, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now, "deactivated_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )
    return {"id": company_id, "message": "Company deactivated successfully"}


@app.get("/health")
def health_check():
    client.admin.command("ping")

    return {
        "status": "ok",
        "message": "HRMS Backend is running",
        "database": MONGODB_DATABASE
    }