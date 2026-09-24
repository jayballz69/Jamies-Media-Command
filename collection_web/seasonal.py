"""Brisbane seasonal windows and reviewed Drift priority; no generated name templates."""
from datetime import date, datetime, timedelta, timezone

BRISBANE = timezone(timedelta(hours=10))
# Queensland Department of Education, checked 2026-09-24:
# https://education.qld.gov.au/about/Pages/termDates.aspx
# https://education.qld.gov.au/about-us/calendar/future-dates
SCHOOL_HOLIDAYS = [
    ("2026-01-01", "2026-01-26"), ("2026-04-03", "2026-04-19"),
    ("2026-06-27", "2026-07-12"), ("2026-09-19", "2026-10-05"),
    ("2026-12-12", "2027-01-26"), ("2027-03-26", "2027-04-11"),
    ("2027-06-26", "2027-07-11"), ("2027-09-18", "2027-10-04"),
    ("2027-12-11", "2028-01-23"), ("2028-04-01", "2028-04-17"),
    ("2028-06-24", "2028-07-09"), ("2028-09-16", "2028-10-02"),
    ("2028-12-09", "2029-01-21"), ("2029-03-30", "2029-04-15"),
    ("2029-06-23", "2029-07-08"), ("2029-09-15", "2029-10-01"),
]
# End of the December 2029 holiday was not published; do not guess it.
DEFAULT_TOGGLES = {"seasonal_enabled": False, "seasonal_halloween": True,
    "seasonal_christmas": True, "seasonal_easter": True, "seasonal_st_patrick": True,
    "seasonal_qld_school": True, "seasonal_new_year": False,
    "seasonal_valentine": False, "seasonal_star_wars": False}

DIRECTION = """Use seasonal_events as the current Brisbane calendar, not as naming templates.
During major holiday windows, make most proposed shelves genuinely fit the occasion,
with several distinct curatorial angles and some unexpected non-seasonal discoveries.
Smaller occasions deserve a few relevant shelves, not a takeover. During Queensland
school holidays give substantial attention to ages 10-16: varied family adventures,
mysteries, comedy, science fiction and overlooked live-action choices, not preschool
filler. Never assume animation or a child protagonist makes a title suitable.
Use supplied content ratings and story evidence; separate younger-family viewing
from older-teen themes, and exclude adult/restricted or uncertain material from
school-holiday endorsements. When occasions overlap, blend them where the actual
items support it, without losing either audience. Normal fit, ownership and quality
checks still apply. Never invent a seasonal connection just to fill slots.
Keep names authored and specific to the selected items, not generic holiday labels.
"""


def local_day(now=None):
    if isinstance(now, datetime):
        return now.astimezone(BRISBANE).date()
    if isinstance(now, date):
        return now
    return datetime.fromtimestamp(now, BRISBANE).date() if now is not None else datetime.now(BRISBANE).date()


def easter(year):
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4
    f=(b+8)//25; g=(b-f+1)//3; h=(19*a+b-d-g+15)%30
    i=c//4; k=c%4; l=(32+2*e+2*i-h-k)%7; m=(a+11*h+22*l)//451
    month=(h+l-7*m+114)//31
    return date(year,month,(h+l-7*m+114)%31+1)


def active_events(settings, now=None):
    toggles = {**DEFAULT_TOGGLES, **settings.get("advanced", {})}
    if not toggles["seasonal_enabled"]:
        return []
    today=local_day(now); events=[]
    def add(kind,label,start,end,weight,focus):
        if toggles.get("seasonal_"+kind) and start <= today <= end:
            events.append({"id":kind+":"+start.isoformat(), "kind":kind,"label":label,
                           "start":start.isoformat(),"end":end.isoformat(),"weight":weight,"focus":focus})
    for year in (today.year-1,today.year):
        add("halloween","Halloween",date(year,10,17),date(year,10,31),3,"Distinct spooky, supernatural, horror and lighter Halloween shelves; separate adult scares from family choices.")
        add("christmas","Christmas",date(year,12,11),date(year,12,25),3,"Christmas stories and genuinely festive connections; family and adult moods kept distinct.")
        sunday=easter(year)
        add("easter","Easter",sunday-timedelta(days=7),sunday+timedelta(days=1),3,"Easter, springtime stories where appropriate, renewal and family adventures; Australia is in autumn, not spring.")
        add("st_patrick","St Patrick's Day",date(year,3,14),date(year,3,17),1,"Irish stories, settings, folklore and verified Irish creative voices; no alcohol-first child collections.")
        add("new_year","New Year",date(year,12,29),date(year+1,1,1),1,"Fresh starts, reinvention and New Year stories.")
        add("valentine","Valentine's Day",date(year,2,11),date(year,2,14),1,"Distinct romance, unlikely connections and alternate takes on love.")
        add("star_wars","May the Fourth",date(year,5,2),date(year,5,4),1,"Verified Star Wars stories and earned adjacent space adventures; do not pretend unrelated titles belong to the franchise.")
    for start,end in SCHOOL_HOLIDAYS:
        add("qld_school","Queensland school holidays",date.fromisoformat(start),date.fromisoformat(end),2,"Family and tween/teen viewing for ages 10-16, with younger and older audiences distinguished.")
    return events


def context_key(events):
    return "|".join(sorted(event["id"] for event in events))


def seasonal_rank(candidate, events):
    weights={e["id"]:e["weight"] for e in events}
    return -max((weights.get(tag.get("id"),0) for tag in candidate.get("seasonal_events",[]) if tag.get("reason")),default=0)


def status(settings, now=None):
    day=local_day(now)
    return {"timezone":"Australia/Brisbane","date":day.isoformat(),"active":active_events(settings,day),
            "school_calendar_current":date(2026,1,1)<=day<=date(2029,10,1),
            "school_calendar_through":"2029-10-01"}
