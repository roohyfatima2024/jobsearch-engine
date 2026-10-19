import re
from datetime import datetime, timezone


def parse_salary(text):
    """Extract clearly stated annual USD salary ranges."""
    if not text:
        return None

    text = str(text)
    pattern = (
        r"\$\s*([\d,]+)\s*(k)?\s*"
        r"(?:-|–|—|to)\s*"
        r"\$?\s*([\d,]+)\s*(k)?"
    )

    match = re.search(pattern, text, re.I)
    if not match:
        return None

    low, low_k, high, high_k = match.groups()
    low = float(low.replace(",", ""))
    high = float(high.replace(",", ""))

    if low_k:
        low *= 1000
    if high_k:
        high *= 1000

    if 20000 <= low <= high <= 1000000:
        return low, high

    return None


def parse_experience(text):
    """Find explicit minimum years of experience."""
    matches = re.findall(
        r"(\d{1,2})\+?\s*(?:years|yrs)\s+of\s+experience",
        text or "",
        re.I,
    )
    return max(map(int, matches)) if matches else None


def check_job(job, cfg):
    """
    Discovery filter aligned with the classifier.

    Exclude only confirmed mismatches.
    Preserve uncertain jobs for manual review.
    """
    from app.job_classifier import classify_job

    classification, reasons = classify_job(job, cfg)

    if classification == "excluded":
        return False, reasons

    # Keep eligible, needs_review, and low_match jobs.
    # Do not reject based on salary or sponsorship.
    return True, reasons
