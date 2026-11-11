"""Tests for the pure mapping logic. Run with:  python -m pytest"""

from app import mapping
from app.forager import ForagerClient, build_title_query


def test_clean_domain():
    assert mapping.clean_domain("https://www.OpenAI.com/about") == "openai.com"
    assert mapping.clean_domain("openai.com") == "openai.com"
    assert mapping.clean_domain(None) == ""


def test_linkedin_identifier():
    assert mapping.linkedin_identifier("https://www.linkedin.com/in/jane-doe/?x=1") == "jane-doe"
    assert mapping.linkedin_identifier("https://example.com") == ""


def test_split_location_from_name():
    location = {"name": "San Francisco, California, United States", "osm_locations": []}
    assert mapping.split_location(location) == {
        "city": "San Francisco", "state": "California", "country": "United States",
    }


def test_split_location_prefers_typed_parts():
    location = {
        "name": "Bay Area",
        "osm_locations": [
            {"name": "United States", "place_type": "country"},
            {"name": "California", "place_type": "state"},
            {"name": "Oakland", "place_type": "city"},
        ],
    }
    assert mapping.split_location(location) == {
        "city": "Oakland", "state": "California", "country": "United States",
    }


def test_split_location_ignores_messy_name_when_typed_parts_exist():
    location = {
        "name": "united states, bangladesh",
        "osm_locations": [{"name": "Bangladesh", "place_type": "country"}],
    }
    assert mapping.split_location(location) == {"city": "", "state": "", "country": "Bangladesh"}


def test_title_matches_whole_words_only():
    assert mapping.title_matches("VP Finance", ["VP", "Director"])
    assert mapping.title_matches("Technical Director", ["director"])
    assert not mapping.title_matches("Team Manager", ["VP", "Director"])
    assert not mapping.title_matches("MVP Engineer", ["VP"])
    assert mapping.title_matches("Anything", [])


def test_company_properties_drop_empty_values():
    org = {
        "id": 7, "name": "OpenAI", "domain": "openai.com", "description": None,
        "employees_amount": 3000, "finance_info": {"revenue": 1000000},
        "founded_date": "2015-12-11", "linkedin_info": {}, "addresses": [], "keywords": [],
    }
    properties = mapping.company_properties(org)
    assert properties["numberofemployees"] == 3000
    assert properties["annualrevenue"] == 1000000
    assert properties["founded_year"] == "2015"
    assert "description" not in properties


def test_current_role_prefers_searched_company():
    person = {"roles": [
        {"is_current": True, "role_title": "Advisor", "start_date": "2024-01-01",
         "organization": {"id": 2}},
        {"is_current": True, "role_title": "CTO", "start_date": "2020-01-01",
         "organization": {"id": 1}},
        {"is_current": False, "role_title": "Intern", "organization": {"id": 1}},
    ]}
    assert mapping.current_role(person, preferred_org_id=1)["role_title"] == "CTO"
    assert mapping.current_role(person)["role_title"] == "Advisor"


def test_contact_properties_and_completeness():
    person = {
        "id": 1, "first_name": "Jane", "last_name": "Doe", "headline": "CTO",
        "location": {"name": "Austin, Texas, United States", "osm_locations": []},
        "linkedin_info": {"public_profile_url": "https://www.linkedin.com/in/jane-doe"},
    }
    role = {"role_title": "CTO", "organization": {
        "name": "Acme", "domain": "https://acme.com",
        "linkedin_info": {"public_profile_url": "https://www.linkedin.com/company/acme"},
    }}
    properties = mapping.contact_properties(person, role, email="jane@acme.com", phone="+1555")
    assert properties["company_domain"] == "acme.com"
    assert properties["company_linkedin_url"].endswith("/company/acme")
    assert mapping.is_complete(properties)
    assert not mapping.is_complete(mapping.contact_properties(person, role))


def test_build_title_query():
    assert build_title_query(["VP Sales", " Head of Sales "]) == '"VP Sales" OR "Head of Sales"'


def test_first_email_handles_both_shapes():
    assert ForagerClient._first_email([{"email": "a@x.com", "validation_status": "valid"}]) == "a@x.com"
    assert ForagerClient._first_email([["a@x.com", "invalid"], ["b@x.com", "valid"]]) == "b@x.com"
    assert ForagerClient._first_email([]) == ""


def test_match_score_requires_exact_domain():
    score = ForagerClient._match_score
    assert score({"text": "OpenAI - openai.com"}, "openai.com", "OpenAI") == 4
    assert score({"text": "ChatGPT - openai.com"}, "openai.com", "OpenAI") == 2
    assert score({"text": "Deploy - deploy.com.pk"}, "deploy.co", "") == 0
    assert score({"text": "OpenAI - openai.com"}, "", "OpenAI") == 2
