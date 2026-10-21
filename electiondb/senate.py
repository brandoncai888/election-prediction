"""US Senate seats not on the 2026 ballot, so chamber totals can add to 100.

Source: Wikipedia "List of current United States senators", retrieved
2026-10-21 (party and next-election columns). Class I seats (next election
2030) and Class III seats (next election 2028) are holdovers; the FL and OH
Class III seats are excluded because they hold 2026 special elections.
Independents King (ME) and Sanders (VT) caucus with Democrats.
"""
from __future__ import annotations

SOURCE_URL = "https://en.wikipedia.org/wiki/List_of_current_United_States_senators"
RETRIEVED = "2026-10-21"
SENATE_SEATS = 100
# The Vice President (JD Vance, Republican) breaks 50-50 ties.
TIEBREAK_PARTY = "R"

# (state, senator, party, class, next regular election)
HOLDOVERS = (
    ('Alabama', 'Katie Britt', 'R', 3, 2028),
    ('Alaska', 'Lisa Murkowski', 'R', 3, 2028),
    ('Arizona', 'Mark Kelly', 'D', 3, 2028),
    ('Arizona', 'Ruben Gallego', 'D', 1, 2030),
    ('Arkansas', 'John Boozman', 'R', 3, 2028),
    ('California', 'Alex Padilla', 'D', 3, 2028),
    ('California', 'Adam Schiff', 'D', 1, 2030),
    ('Colorado', 'Michael Bennet', 'D', 3, 2028),
    ('Connecticut', 'Richard Blumenthal', 'D', 3, 2028),
    ('Connecticut', 'Chris Murphy', 'D', 1, 2030),
    ('Delaware', 'Lisa Blunt Rochester', 'D', 1, 2030),
    ('Florida', 'Rick Scott', 'R', 1, 2030),
    ('Georgia', 'Raphael Warnock', 'D', 3, 2028),
    ('Hawaii', 'Brian Schatz', 'D', 3, 2028),
    ('Hawaii', 'Mazie Hirono', 'D', 1, 2030),
    ('Idaho', 'Mike Crapo', 'R', 3, 2028),
    ('Illinois', 'Tammy Duckworth', 'D', 3, 2028),
    ('Indiana', 'Todd Young', 'R', 3, 2028),
    ('Indiana', 'Jim Banks', 'R', 1, 2030),
    ('Iowa', 'Chuck Grassley', 'R', 3, 2028),
    ('Kansas', 'Jerry Moran', 'R', 3, 2028),
    ('Kentucky', 'Rand Paul', 'R', 3, 2028),
    ('Louisiana', 'John Kennedy', 'R', 3, 2028),
    ('Maine', 'Angus King', 'I', 1, 2030),
    ('Maryland', 'Chris Van Hollen', 'D', 3, 2028),
    ('Maryland', 'Angela Alsobrooks', 'D', 1, 2030),
    ('Massachusetts', 'Elizabeth Warren', 'D', 1, 2030),
    ('Michigan', 'Elissa Slotkin', 'D', 1, 2030),
    ('Minnesota', 'Amy Klobuchar', 'D', 1, 2030),
    ('Mississippi', 'Roger Wicker', 'R', 1, 2030),
    ('Missouri', 'Josh Hawley', 'R', 1, 2030),
    ('Missouri', 'Eric Schmitt', 'R', 3, 2028),
    ('Montana', 'Tim Sheehy', 'R', 1, 2030),
    ('Nebraska', 'Deb Fischer', 'R', 1, 2030),
    ('Nevada', 'Catherine Cortez Masto', 'D', 3, 2028),
    ('Nevada', 'Jacky Rosen', 'D', 1, 2030),
    ('New Hampshire', 'Maggie Hassan', 'D', 3, 2028),
    ('New Jersey', 'Andy Kim', 'D', 1, 2030),
    ('New Mexico', 'Martin Heinrich', 'D', 1, 2030),
    ('New York', 'Chuck Schumer', 'D', 3, 2028),
    ('New York', 'Kirsten Gillibrand', 'D', 1, 2030),
    ('North Carolina', 'Ted Budd', 'R', 3, 2028),
    ('North Dakota', 'John Hoeven', 'R', 3, 2028),
    ('North Dakota', 'Kevin Cramer', 'R', 1, 2030),
    ('Ohio', 'Bernie Moreno', 'R', 1, 2030),
    ('Oklahoma', 'James Lankford', 'R', 3, 2028),
    ('Oregon', 'Ron Wyden', 'D', 3, 2028),
    ('Pennsylvania', 'John Fetterman', 'D', 3, 2028),
    ('Pennsylvania', 'Dave McCormick', 'R', 1, 2030),
    ('Rhode Island', 'Sheldon Whitehouse', 'D', 1, 2030),
    ('South Carolina', 'Tim Scott', 'R', 3, 2028),
    ('South Dakota', 'John Thune', 'R', 3, 2028),
    ('Tennessee', 'Marsha Blackburn', 'R', 1, 2030),
    ('Texas', 'Ted Cruz', 'R', 1, 2030),
    ('Utah', 'Mike Lee', 'R', 3, 2028),
    ('Utah', 'John Curtis', 'R', 1, 2030),
    ('Vermont', 'Bernie Sanders', 'I', 1, 2030),
    ('Vermont', 'Peter Welch', 'D', 3, 2028),
    ('Virginia', 'Tim Kaine', 'D', 1, 2030),
    ('Washington', 'Patty Murray', 'D', 3, 2028),
    ('Washington', 'Maria Cantwell', 'D', 1, 2030),
    ('West Virginia', 'Jim Justice', 'R', 1, 2030),
    ('Wisconsin', 'Ron Johnson', 'R', 3, 2028),
    ('Wisconsin', 'Tammy Baldwin', 'D', 1, 2030),
    ('Wyoming', 'John Barrasso', 'R', 1, 2030),
)


def holdover_counts():
    """Seats not up in 2026 by caucus: independents counted with Democrats."""
    caucus = {"D": 0, "R": 0}
    for _, _, party, _, _ in HOLDOVERS:
        caucus["R" if party == "R" else "D"] += 1
    return {"D": caucus["D"], "R": caucus["R"], "independents_caucusing_D": sum(p == "I" for _, _, p, _, _ in HOLDOVERS),
            "total": len(HOLDOVERS), "source_url": SOURCE_URL, "retrieved": RETRIEVED}


def control(dem_seats):
    """Chamber control from total Democratic-caucus seats out of 100."""
    if dem_seats >= 51:
        return "D"
    if dem_seats == 50:
        return TIEBREAK_PARTY
    return "R"
