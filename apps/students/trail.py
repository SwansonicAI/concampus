from django.urls import reverse


def above(person, study):
    """The trail to a study's Akte, the Akte last: from the list of students, the teacher's own, or one's own studies."""
    if study.person == person:
        return [('Mein Studium', study.get_absolute_url())]
    first = ('Studierende', reverse('students:list')) if 'view_students' in person.roles else ('Meine Lehre', reverse('courses:mine'))
    return [first, (f'{study.person.first_name} {study.person.last_name}', study.get_absolute_url())]
