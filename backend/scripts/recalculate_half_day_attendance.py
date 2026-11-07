"""Recalculate historical automatic attendance with the current half-day rules.

Run from backend/:  python scripts/recalculate_half_day_attendance.py [--from YYYY-MM-DD]
                    [--to YYYY-MM-DD] [--apply]

Without --apply nothing is written; the script only reports how many records are
eligible. Only ESSL / WFH_FACE_GPS records that HR Admin has not edited are
eligible. Records in a payroll month that is Approved, Processed or Paid are
always skipped, as are manual, admin-edited and attendance-request records.
Each eligible day is recalculated by the same function the application uses
(app.attendance.refresh_automatic_attendance_calculation), so the stored result
matches Daily, Monthly, Calendar, History, Excel export and payroll.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.attendance import attendance_collection, refresh_automatic_attendance_calculation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="start", default="1900-01-01", help="first attendance date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="end", default=date.today().isoformat(), help="last attendance date (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true", help="write the recalculated values")
    args = parser.parse_args()

    query = {
        "attendance_source": {"$in": ["ESSL", "WFH_FACE_GPS"]},
        "hr_admin_modified": {"$ne": True},
        "attendance_date": {"$gte": args.start, "$lte": args.end},
    }
    records = list(attendance_collection.find(query, {"user_id": 1, "attendance_date": 1}))
    print(f"Eligible automatic records between {args.start} and {args.end}: {len(records)}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to recalculate (payroll-locked months are skipped).")
        return 0

    updated = unchanged_or_skipped = failed = 0
    for record in records:
        try:
            changed = refresh_automatic_attendance_calculation(
                str(record["user_id"]), date.fromisoformat(str(record["attendance_date"])[:10])
            )
        except Exception as error:  # keep going; report at the end
            failed += 1
            print(f"FAILED {record.get('user_id')} {record.get('attendance_date')}: {error}")
            continue
        if changed:
            updated += 1
        else:
            unchanged_or_skipped += 1
    print(f"Updated: {updated}  Unchanged/skipped (incl. payroll-locked): {unchanged_or_skipped}  Failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
