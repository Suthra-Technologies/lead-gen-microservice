"""
Pydantic Data Models
Provides strict typing and data validation for harvested solar buyer leads,
including content classification, freshness tiers, buyer stages, and verifiable evidence snippets.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class LeadCategory(str, Enum):
    HOT_BUYER = "HOT_BUYER"
    WARM_BUYER = "WARM_BUYER"
    EXISTING_CUSTOMER_EXPANSION = "EXISTING_CUSTOMER_EXPANSION"
    RESEARCHING = "RESEARCHING"
    NON_BUYER = "NON_BUYER"


class LeadSegment(str, Enum):
    NEW_BUYER = "NEW_BUYER"                   # First-time solar installation / Quote seekers
    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"   # Additional panels / Battery storage
    SERVICE_CUSTOMER = "SERVICE_CUSTOMER"     # Solar repairs / Inverter replacement / Maintenance
    NON_BUYER = "NON_BUYER"


class BuyerStage(str, Enum):
    SEEKING_QUOTES = "SEEKING_QUOTES"
    COMPARING_QUOTES = "COMPARING_QUOTES"
    PLANNING = "PLANNING"
    READY_TO_PURCHASE = "READY_TO_PURCHASE"
    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"
    SERVICE_REQUIRED = "SERVICE_REQUIRED"
    EXPLORING = "EXPLORING"
    UNKNOWN = "UNKNOWN"


class ClassificationType(str, Enum):
    RESIDENTIAL_NEW_BUYER = "RESIDENTIAL_NEW_BUYER"
    RESIDENTIAL_QUOTE_SEEKER = "RESIDENTIAL_QUOTE_SEEKER"
    RESIDENTIAL_EXISTING_OWNER = "RESIDENTIAL_EXISTING_OWNER"
    RESIDENTIAL_UPGRADE = "RESIDENTIAL_UPGRADE"
    RESIDENTIAL_REPAIR = "RESIDENTIAL_REPAIR"
    COMMERCIAL_SOLAR = "COMMERCIAL_SOLAR"
    DIY_OFF_GRID = "DIY_OFF_GRID"
    TECHNICAL_DISCUSSION = "TECHNICAL_DISCUSSION"
    GENERAL_INFORMATION = "GENERAL_INFORMATION"
    SPAM_VENDOR = "SPAM_VENDOR"


class LeadFreshness(str, Enum):
    DAYS_0_7 = "0-7 days"
    DAYS_8_30 = "8-30 days"
    DAYS_31_90 = "31-90 days"
    OLDER_90 = "Older than 90 days"
    UNKNOWN = "Unknown"


class LeadLifecycleStatus(str, Enum):
    NEW = "NEW"
    PENDING_REVIEW = "PENDING_REVIEW"
    QUALIFIED = "QUALIFIED"
    ASSIGNED = "ASSIGNED"
    CONTACTED = "CONTACTED"
    INTERESTED = "INTERESTED"
    SITE_ASSESSMENT = "SITE_ASSESSMENT"
    PROPOSAL_SENT = "PROPOSAL_SENT"
    LOST = "LOST"
    WON = "WON"


class QualificationStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    PENDING_REVIEW = "PENDING_REVIEW"
    DISQUALIFIED = "DISQUALIFIED"


class ValidationStatus(str, Enum):
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"


class SolarLead(BaseModel):
    source_name: str = Field(default="Web Discussion", description="Name of forum or website")
    source_url: str = Field(default="", description="Base domain or seed URL")
    post_url: str = Field(description="Direct link to the discussion thread")
    author: Optional[str] = Field(default="Anonymous", description="Author handle or username")
    post_title: Optional[str] = Field(default="", description="Discussion title")
    intent_snippet: Optional[str] = Field(default="", description="Contextual excerpt of purchase intent")
    lead_score: float = Field(ge=-20.0, le=50.0, description="Calculated intent score")
    lead_category: str = Field(default=LeadCategory.RESEARCHING.value)
    lead_segment: str = Field(default=LeadSegment.NEW_BUYER.value, description="NEW_BUYER, EXISTING_CUSTOMER, SERVICE_CUSTOMER")
    classification_type: str = Field(default=ClassificationType.RESIDENTIAL_NEW_BUYER.value)
    primary_classification: str = Field(default=ClassificationType.RESIDENTIAL_NEW_BUYER.value)
    secondary_classifications: List[str] = Field(default_factory=list)
    lead_freshness: str = Field(default=LeadFreshness.UNKNOWN.value)
    published_at: Optional[str] = Field(default=None, description="Extracted date of thread creation")
    latest_reply_at: Optional[str] = Field(default=None, description="Extracted date of latest reply in thread")
    
    # 3-Timestamp Intelligence & Dual Freshness
    thread_created_at: Optional[str] = Field(default=None, description="Thread creation date")
    latest_thread_activity_at: Optional[str] = Field(default=None, description="Most recent reply date in thread")
    last_buyer_intent_at: Optional[str] = Field(default=None, description="Specific timestamp when buyer intent occurred")
    thread_freshness: str = Field(default=LeadFreshness.UNKNOWN.value)
    buyer_intent_freshness: str = Field(default=LeadFreshness.UNKNOWN.value)
    
    # Multi-Dimensional Qualification Confidence Scores (0.0 to 1.0, intent 0.0 to 10.0)
    purchase_intent_score: float = Field(default=0.0, ge=0.0, le=10.0, description="Calibrated intent score 0-10")
    residential_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence that target is residential")
    location_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence of extracted location")
    freshness_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Normalized freshness factor")
    evidence_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence of extracted intent evidence")
    composite_qualification_score: float = Field(default=0.0, ge=0.0, le=100.0, description="Standardized 100-pt qualification formula Q")
    hard_disqualified: bool = Field(default=False)
    disqualification_reason: Optional[str] = Field(default=None)
    needs_human_review: bool = Field(default=False)
    review_reason: Optional[str] = Field(default=None)
    
    # Lead Lifecycle & CRM Integration
    lead_status: str = Field(default=LeadLifecycleStatus.NEW.value, description="CRM Lifecycle state")
    qualification_status: str = Field(default=QualificationStatus.QUALIFIED.value)
    validation_status: str = Field(default=ValidationStatus.PASSED.value)
    validation_flags: List[str] = Field(default_factory=list)
    assigned_contractor_id: Optional[str] = Field(default=None)

    # Property & Solar Opportunity Intelligence
    property_type: Optional[str] = Field(default=None, description="e.g. Single Family Residential or Commercial")
    country: str = Field(default="USA")
    state: Optional[str] = Field(default=None, description="2-letter US state code")
    city: Optional[str] = Field(default=None)
    zip_code: Optional[str] = Field(default=None)
    system_size_kw: Optional[float] = Field(default=None, ge=0.5, le=500.0, description="System capacity in kW")
    electricity_bill: Optional[float] = Field(default=None, ge=10.0, le=10000.0, description="Monthly power bill")
    estimated_monthly_usage_kwh: Optional[float] = Field(default=None)
    estimated_annual_usage_kwh: Optional[float] = Field(default=None)
    estimated_system_size_kw: Optional[float] = Field(default=None)
    estimated_solar_offset_percent: Optional[float] = Field(default=None)

    buyer_stage: str = Field(default=BuyerStage.UNKNOWN.value)
    matched_keywords: List[str] = Field(default_factory=list)
    evidence_snippets: List[str] = Field(default_factory=list, description="Specific evidence sentences justifying score")
    crawled_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("intent_snippet")
    @classmethod
    def truncate_snippet(cls, v: Optional[str]) -> str:
        if v and len(v) > 1500:
            return v[:1500] + "..."
        return v or ""


class ContractorMatch(BaseModel):
    place_id: str
    company_name: str
    rating: Optional[float] = None
    phone_number: Optional[str] = None
    website_url: Optional[str] = None
    match_score: int
    match_reasons: List[str] = Field(default_factory=list)


class SolarOpportunity(BaseModel):
    electricity_bill: Optional[float] = None
    rate_per_kwh: float = 0.16
    estimated_monthly_usage_kwh: Optional[float] = None
    estimated_annual_usage_kwh: Optional[float] = None
    estimated_system_size_kw: Optional[float] = None
    estimated_solar_offset_percent: Optional[float] = None
    calculation_basis: str = "ESTIMATED"


class LeadValidationResult(BaseModel):
    is_valid: bool
    status: str = "PASSED"
    flags: List[str] = Field(default_factory=list)


class LeadEvidence(BaseModel):
    evidence_id: Optional[int] = None
    lead_id: Optional[int] = None
    source_url: str
    source_author: Optional[str] = "Anonymous"
    evidence_text: str
    evidence_type: str = "PURCHASE_INTENT"
    evidence_polarity: str = "POSITIVE"
    published_at: Optional[str] = None
    extracted_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    extraction_method: str = "RULE_REGEX_V1"
    model_version: str = "2.0.0"
    confidence: float = 1.0
    score_contribution: float = 0.0


class IntentEvent(BaseModel):
    intent_event_id: Optional[int] = None
    lead_id: Optional[int] = None
    evidence_id: Optional[int] = None
    intent_type: str = "PURCHASE_INTENT"
    intent_polarity: str = "POSITIVE"
    intent_published_at: Optional[str] = None
    intent_confidence: float = 1.0
    recorded_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))


class LeadReviewRecord(BaseModel):
    review_id: Optional[int] = None
    lead_id: int
    review_reason: str
    review_priority: str = "NORMAL"
    review_status: str = "PENDING"
    assigned_reviewer: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_at: Optional[datetime] = None
    reviewer_notes: Optional[str] = None


class SourceCompliance(BaseModel):
    source_id: str
    terms_url: Optional[str] = None
    robots_policy_status: str = "PERMITTED"
    api_policy_status: str = "COMPLIANT"
    permitted_collection_method: str = "PUBLIC_HTML"
    commercial_use_status: str = "RESTRICTED_REVIEW"
    reviewed_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_by: str = "SYSTEM_AUDITOR"


class BusinessContactType(str, Enum):
    BUSINESS_EMAIL = "BUSINESS_EMAIL"
    BUSINESS_PHONE = "BUSINESS_PHONE"
    CONTACT_FORM = "CONTACT_FORM"
    VOLUNTARY_PUBLIC_CONTACT = "VOLUNTARY_PUBLIC_CONTACT"


class BusinessContact(BaseModel):
    source_name: str = Field(description="Name of publishing organization or site")
    source_url: str = Field(description="URL where contact was found")
    company_name: Optional[str] = Field(default=None)
    contact_type: str = Field(default=BusinessContactType.BUSINESS_EMAIL.value)
    contact_value: str = Field(description="Email, formatted phone number, form URL, or voluntary contact")
    author_username: Optional[str] = Field(default=None, description="Forum username or author handle")
    source_permalink: Optional[str] = Field(default=None, description="Original discussion permalink")
    evidence_snippet: Optional[str] = Field(default=None)
    verification_status: str = Field(default="VERIFIED_PUBLIC_BUSINESS")
    discovered_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))


class SolarPlaceLead(BaseModel):
    place_id: str = Field(description="Unique Google Place ID")
    company_name: str = Field(description="Company or contractor name")
    formatted_address: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    zip_code: Optional[str] = Field(default=None)
    phone_number: Optional[str] = Field(default=None)
    website_url: Optional[str] = Field(default=None)
    google_maps_url: Optional[str] = Field(default=None)
    rating: Optional[float] = Field(default=None)
    user_ratings_total: Optional[int] = Field(default=None)
    business_status: Optional[str] = Field(default="OPERATIONAL")
    primary_type: Optional[str] = Field(default=None)
    metro_area: Optional[str] = Field(default=None)
    search_query: Optional[str] = Field(default=None)
    crawled_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))


