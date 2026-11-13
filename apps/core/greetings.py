"""The start page's greeting: short, from all over the German-speaking world, now and then from the stage; by the time of
day, a new one each time. A greeting without {name} gets ', {name}' after it."""
import random

ANYTIME = [
    'Moin', 'Moinsen', 'Servus', 'Servas', 'Grüß Gott', 'Grüß Sie', 'Habedere', 'Grüezi', 'Grüessech', 'Hoi', 'Salü', 'Salut',
    'Gude', 'Ei gude', 'Tach', 'Tagchen', 'Glück auf', 'Ahoi', 'Hallo', 'Griaß di',
    'Da capo, {name}!', 'Bühne frei, {name}', 'Vorhang auf, {name}',
]
TIMED = [  # from this hour on; before the first, the last
    (5, ['Guten Morgen', 'Moin moin', 'Früh dran, {name}', 'Morgenstund, {name}']),  # an early start
    (8, ['Guten Morgen', 'Schönen guten Morgen', 'Morgen', 'Morjen', 'Moin moin']),
    (11, ['Mahlzeit', 'Schönen Mittag']),
    (14, ['Guten Tag', 'Juten Tach', 'Schönen Nachmittag', 'Tag, {name}']),
    (18, ['Guten Abend', 'Schönen Abend', "'n Abend"]),
    (22, ['Guten Abend', "'n Abend"]),
]


def greeting(person, now):
    """Half the time one for the hour of `now` (local time), else one for any time."""
    timed = next((words for hour, words in reversed(TIMED) if now.hour >= hour), TIMED[-1][1])
    words = random.choice(timed if random.random() < 0.5 else ANYTIME)
    return (words if '{name}' in words else words + ', {name}').format(name=person.first_name or person.username)
