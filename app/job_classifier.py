import re
from datetime import datetime, timezone, timedelta
from app.job_filters import parse_experience


US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE",
    "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC"
}

NON_US_TERMS = (
    "india", "bangalore", "bengaluru", "hyderabad",
    "pune", "mumbai", "chennai", "delhi", "noida",
    "gurugram", "gurgaon", "kolkata", "ahmedabad",
    "kochi", "canada", "united kingdom", "london",
    "germany", "france", "australia"
)


def location_status(location):
    """
    us: clearly US-based
    non_us: clearly outside the US
    unknown: remote without geography, mixed, or unclear
    """
    location = str(location or "").strip()
    lower = location.lower()

    if not lower:
        return "unknown"

    # Explicit country references.
    us_country = bool(re.search(
        r"\b(united states|usa|u\.s\.a\.|u\.s\.|"
        r"remote[ ,()-]*us|us[ ,()-]*remote|us only)\b",
        lower
    ))

    # Recognizable US state names and state abbreviations.
    us_names = (
        "california", "washington", "texas", "new york",
        "colorado", "florida", "massachusetts",
        "virginia", "illinois", "north carolina",
        "new jersey", "arizona", "oregon", "nevada",
        "ohio", "pennsylvania"
    )

    us_state = (
        any(re.search(r"\b" + re.escape(s) + r"\b", lower)
            for s in us_names)
        or bool(re.search(
            r",\s*(" + "|".join(sorted(US_STATES)) + r")\b",
            location, re.I
        ))
    )

    has_us = us_country or us_state

    international = NON_US_TERMS + (
        "brazil", "sao paulo", "são paulo",
        "japan", "tokyo", "osaka",
        "spain", "madrid", "italy", "rome",
        "ireland", "dublin", "poland",
        "israel", "singapore", "mexico",
        "argentina", "philippines",
        "netherlands", "portugal",
        "switzerland", "south africa",
        "united arab emirates"
    )

    has_non_us = any(
        re.search(r"\b" + re.escape(term) + r"\b", lower)
        for term in international
    )

    if has_us and has_non_us:
        return "unknown"

    if has_us:
        return "us"

    if has_non_us:
        return "non_us"

    return "unknown"


def posting_date_status(job, cfg):
    # Only use explicit employer posting timestamps.
    # discovered_at is NOT the employer posting date.
    value = None
    for key in ("posted_at", "published_at", "date_posted"):
        if job.get(key):
            value = job[key]
            break

    if not value:
        return "unknown"

    try:
        if isinstance(value, (int, float)):
            posted = datetime.fromtimestamp(value, timezone.utc)
        else:
            posted = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - posted
        if age < timedelta(days=-1):
            return "unknown"
        if age > timedelta(
            days=cfg.get("maximum_posting_age_days", 7)
        ):
            return "old"

        return "recent"
    except (ValueError, TypeError, OverflowError):
        return "unknown"


def classify_job(job, cfg):
    title = str(job.get("title") or "").lower()
    description = str(job.get("description") or "")
    notes = []

    targets = cfg.get("target_title_terms", [])
    excluded = cfg.get("excluded_title_terms", [])

    if any(term.lower() in title for term in excluded):
        return "excluded", ["Excluded job title"]

    if not any(term.lower() in title for term in targets):
        return "excluded", ["Not a target IT/CS role"]

    location = location_status(job.get("location"))

    if location == "non_us":
        return "excluded", ["Non-US location"]

    if location == "unknown":
        notes.append("US location requires verification")

    required = parse_experience(description)
    preferred = cfg.get("maximum_experience_years", 6)

    if required is None:
        notes.append("Experience requirement unverified")
    elif required > preferred:
        notes.append(
            f"Experience requirement: {required} years; "
            f"preferred maximum: {preferred}"
        )

    freshness = posting_date_status(job, cfg)

    if freshness == "old":
        return "excluded", ["Posting older than 7 days"]

    if freshness == "unknown":
        notes.append("Employer posting date requires verification")

    if notes:
        return "needs_review", notes

    return "eligible", ["Passed screening"]
