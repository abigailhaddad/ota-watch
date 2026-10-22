"""Other Transaction (OT) documents from FPDS's public ATOM feed.

FPDS is the only public source of OT awards: they are not FAR contracts, and
USAspending carries none of them. The feed needs no key. A query returns 10
entries a page; `fetch_window` pages through one signed-date window.

The query AWARD_TYPE:"OTHER TRANSACTION" returns three kinds of documents:
OtherTransactionAward with action type OTHER TRANSACTION AGREEMENT or OTHER
TRANSACTION ORDER (an order is placed under an OT IDV), and
OtherTransactionIDV (the parent vehicle). Every document is one action: the
base award or a modification.

Dollars: only `obligated` (the money obligated by this one action) is used
for anything shown. FPDS's "total" and "base and all options" fields on OT
documents are kept as raw columns but are unreliable (one DoD OT reads
$100M, $281M and $351.6M in different fields of the same record chain).

Defense OT documents become public about 90 days after they are signed (the
DoD release delay); other agencies' appear within days.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger(__name__)

URL = "https://www.fpds.gov/ezsearch/FEEDS/ATOM"
PAGE = 10  # FPDS's fixed page size
NS = {"a": "http://www.w3.org/2005/Atom", "f": "https://www.fpds.gov/FPDS"}

# one row per document; the order of the stored table
FIELDS = [
    "key", "kind", "action_type", "agency_id", "piid", "mod", "parent_agency_id", "parent_piid",
    "signed", "effective", "completion", "fiscal_year", "obligated",
    "total_obligated_raw", "total_all_options_raw",
    "agreement_type", "mod_reason", "description", "nontraditional", "competed", "solicitation",
    "department", "contracting_agency", "contracting_office_id", "contracting_office",
    "funding_agency", "funding_office",
    "vendor", "uei", "parent_uei", "parent_name", "consortium", "psc", "psc_desc",
    "created", "last_modified", "fpds_link",
]


def _t(el, path):
    x = el.find(path, NS) if el is not None else None
    return None if x is None or x.text is None else x.text.strip() or None


def _a(el, path, name):
    x = el.find(path, NS) if el is not None else None
    return None if x is None else x.get(name)


def _money(s) -> float | None:
    return None if s in (None, "") else float(s)


def doc_key(kind: str, agency_id, piid, mod, parent_piid) -> str:
    """Unique per FPDS document: order PIIDs repeat across parent IDVs."""
    return "|".join([kind, agency_id or "", piid or "", mod or "0", parent_piid or ""])


def parse_entry(entry) -> dict:
    content = entry.find("a:content", NS)
    doc = list(content)[0]
    kind = doc.tag.split("}")[1]
    if kind == "OtherTransactionIDV":
        idp = "f:OtherTransactionIDVID/f:OtherTransactionIDVContractID"
        parent = None
    else:
        idp = "f:OtherTransactionAwardID/f:OtherTransactionAwardContractID"
        parent = "f:OtherTransactionAwardID/f:referencedIDVID"
    d = doc.find("f:contractDetail", NS)
    dates, dv, tv = "f:relevantContractDates/", "f:dollarValues/", "f:totalDollarValues/"
    cd, pi = "f:contractData/", "f:purchaserInformation/"
    row = {
        "kind": kind,
        "action_type": _a(d, cd + "f:contractActionType", "description"),
        "agency_id": _t(doc, idp + "/f:agencyID"),
        "piid": _t(doc, idp + "/f:PIID"),
        "mod": _t(doc, idp + "/f:modNumber") or "0",
        "parent_agency_id": _t(doc, parent + "/f:agencyID") if parent else None,
        "parent_piid": _t(doc, parent + "/f:PIID") if parent else None,
        "signed": (_t(d, dates + "f:signedDate") or "")[:10] or None,
        "effective": (_t(d, dates + "f:effectiveDate") or "")[:10] or None,
        "completion": (_t(d, dates + "f:currentCompletionDate") or "")[:10] or None,
        "fiscal_year": _t(d, dates + "f:fiscalYear"),
        "obligated": _money(_t(d, dv + "f:obligatedAmount")),
        "total_obligated_raw": _money(_t(d, tv + "f:totalObligatedAmount")),
        "total_all_options_raw": _money(_t(d, tv + "f:totalBaseAndAllOptionsValue")),
        "agreement_type": _t(d, cd + "f:typeOfAgreement"),
        "mod_reason": _a(d, cd + "f:reasonForModification", "description"),
        "description": _t(d, cd + "f:descriptionOfContractRequirement"),
        "nontraditional": _a(d, cd + "f:nonTraditionalGovernmentContractorParticipation", "description"),
        "competed": _a(d, ".//f:competition/f:extentCompeted", "description"),
        "solicitation": _a(d, ".//f:competition/f:solicitationProcedures", "description"),
        "department": _a(d, pi + "f:contractingOfficeAgencyID", "departmentName"),
        "contracting_agency": _a(d, pi + "f:contractingOfficeAgencyID", "name"),
        "contracting_office_id": _t(d, pi + "f:contractingOfficeID"),
        "contracting_office": _a(d, pi + "f:contractingOfficeID", "name"),
        "funding_agency": _a(d, pi + "f:fundingRequestingAgencyID", "name"),
        "funding_office": _a(d, pi + "f:fundingRequestingOfficeID", "name"),
        "vendor": _t(d, ".//f:vendor/f:vendorHeader/f:vendorName"),
        "uei": _t(d, ".//f:vendorUEIInformation/f:UEI"),
        "parent_uei": _t(d, ".//f:vendorUEIInformation/f:ultimateParentUEI"),
        "parent_name": _t(d, ".//f:vendorUEIInformation/f:ultimateParentUEIName"),
        "consortium": _a(d, ".//f:consortiaInformation/f:consortiaFlag", "description")
                      or _t(d, ".//f:consortiaInformation/f:consortiaFlag"),
        "psc": _t(d, ".//f:PSCCode"),
        "psc_desc": _a(d, ".//f:PSCCode", "description"),
        "created": _t(d, ".//f:transactionInformation/f:createdDate"),
        "last_modified": _t(d, ".//f:transactionInformation/f:lastModifiedDate"),
        "fpds_link": (entry.find("a:link", NS).get("href") if entry.find("a:link", NS) is not None else None),
    }
    row["key"] = doc_key(kind, row["agency_id"], row["piid"], row["mod"], row["parent_piid"])
    return {f: row.get(f) for f in FIELDS}


def _get(params: dict, session=None) -> bytes:
    s = session or requests
    for attempt in range(6):
        try:
            r = s.get(URL, params=params, timeout=90)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            if attempt == 5:
                raise
            log.warning("FPDS %s (attempt %d); retrying", e, attempt + 1)
            time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch_window(start: dt.date, end: dt.date, session=None) -> list[dict]:
    """Every OT document signed start..end (inclusive)."""
    q = f'AWARD_TYPE:"OTHER TRANSACTION" SIGNED_DATE:[{start:%Y/%m/%d},{end:%Y/%m/%d}]'
    out, offset = [], 0
    while True:
        root = ET.fromstring(_get({"FEEDNAME": "PUBLIC", "q": q, "start": offset}, session))
        entries = root.findall("a:entry", NS)
        out += [parse_entry(e) for e in entries]
        if len(entries) < PAGE:
            return out
        offset += PAGE


def fetch_range(start: dt.date, end: dt.date, days: int = 7, session=None) -> list[dict]:
    """fetch_window over start..end in windows of `days` (keeps pagination shallow)."""
    s = session or requests.Session()
    out, d = [], start
    while d <= end:
        e = min(d + dt.timedelta(days=days - 1), end)
        got = fetch_window(d, e, s)
        log.info("FPDS OT documents signed %s..%s: %d", d, e, len(got))
        out += got
        d = e + dt.timedelta(days=1)
    return out
