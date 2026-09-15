"""ESPN team IDs for clubs in Europe's five major domestic leagues."""

TEAMS = {
    # Premier League
    "arsenal": ("Arsenal", "359"), "aston villa": ("Aston Villa", "362"),
    "bournemouth": ("Bournemouth", "8678"), "brentford": ("Brentford", "337"),
    "brighton": ("Brighton & Hove Albion", "397"), "burnley": ("Burnley", "379"),
    "chelsea": ("Chelsea", "363"), "crystal palace": ("Crystal Palace", "384"),
    "everton": ("Everton", "368"), "fulham": ("Fulham", "370"),
    "leeds": ("Leeds United", "357"), "liverpool": ("Liverpool", "364"),
    "manchester city": ("Manchester City", "382"), "man city": ("Manchester City", "382"),
    "manchester united": ("Manchester United", "360"), "man united": ("Manchester United", "360"),
    "newcastle": ("Newcastle United", "361"), "nottingham forest": ("Nottingham Forest", "393"),
    "sunderland": ("Sunderland", "366"), "tottenham": ("Tottenham Hotspur", "367"),
    "west ham": ("West Ham United", "371"), "wolves": ("Wolverhampton Wanderers", "380"),
    # La Liga
    "real madrid": ("Real Madrid", "86"), "barcelona": ("FC Barcelona", "83"),
    "atletico madrid": ("Atlético Madrid", "1068"), "athletic bilbao": ("Athletic Club", "93"),
    "real sociedad": ("Real Sociedad", "89"), "villarreal": ("Villarreal", "102"),
    "sevilla": ("Sevilla FC", "243"), "real betis": ("Real Betis", "244"),
    "valencia": ("Valencia CF", "94"), "getafe": ("Getafe", "2922"),
    "girona": ("Girona FC", "9812"), "mallorca": ("Mallorca", "3826"),
    # Bundesliga
    "bayern munich": ("Bayern Munich", "132"), "borussia dortmund": ("Borussia Dortmund", "124"),
    "bayer leverkusen": ("Bayer Leverkusen", "131"), "rb leipzig": ("RB Leipzig", "11420"),
    "eintracht frankfurt": ("Eintracht Frankfurt", "125"), "stuttgart": ("VfB Stuttgart", "134"),
    "wolfsburg": ("VfL Wolfsburg", "138"), "borussia monchengladbach": ("Borussia Mönchengladbach", "123"),
    # Serie A
    "juventus": ("Juventus", "111"), "inter milan": ("Inter Milan", "110"),
    "ac milan": ("AC Milan", "103"), "napoli": ("Napoli", "114"),
    "roma": ("AS Roma", "104"), "lazio": ("Lazio", "115"),
    "atalanta": ("Atalanta", "105"), "fiorentina": ("Fiorentina", "109"),
    # Ligue 1
    "psg": ("Paris Saint-Germain", "160"), "paris saint germain": ("Paris Saint-Germain", "160"),
    "marseille": ("Marseille", "176"), "lyon": ("Lyon", "167"),
    "monaco": ("Monaco", "174"), "lille": ("Lille", "166"),
    "nice": ("Nice", "175"), "rennes": ("Rennes", "169"),
}

def find_team(name: str):
    return TEAMS.get(" ".join(name.casefold().split()))
