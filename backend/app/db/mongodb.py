import os

from dotenv import load_dotenv
from pymongo import MongoClient


load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE")

client = MongoClient(MONGODB_URL)

db = client[MONGODB_DATABASE]


COMPANIES_VALIDATOR = {
	"$jsonSchema": {
		"bsonType": "object",
		"required": ["company", "legal_entity", "branch", "location"],
		"properties": {
			"company": {"bsonType": "string"},
			"legal_entity": {"bsonType": "string"},
			"branch": {"bsonType": "string"},
			"location": {"bsonType": "string"},
		},
	}
}


def ensure_companies_collection():
	if "companies" in db.list_collection_names():
		db.command(
			"collMod",
			"companies",
			validator=COMPANIES_VALIDATOR,
			validationLevel="moderate",
			validationAction="error",
		)
		return

	db.create_collection(
		"companies",
		validator=COMPANIES_VALIDATOR,
		validationLevel="moderate",
		validationAction="error",
	)