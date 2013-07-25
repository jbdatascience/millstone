genome-designer-v2
==================

Second generation of Genome Designer


## Installation

### Python environment

We recommend using [virtualenv](http://pypi.python.org/pypi/virtualenv) for
creating and managing a sandbox python environment. This strategy makes it easy
to stay up with requirements. Our requirements are listed in `requirements.txt`.
Follow the instructions below to setup your virtualenv and install the required
packages.

#### Setting up a virtual python environment

1. Install [virtualenv](http://www.virtualenv.org/en/latest/index.html) if you don't have it yet. (You may want to install [pip](http://pypi.python.org/pypi/pip/) first.)

2. Create a new virtual environment for this project. This virtual environment isn't part of the project so just put it somewhere on your machine.  I keep all of my virtual environments in the directory ~/pyenvs/.

        $ virtualenv ~/pyenvs/genome-designer-env

    If you want to use a version of python different from the OS default you can specify the python binary with the '-p' option:

        $ virtualenv -p /usr/local/bin/python2.7 ~/pyenvs/genome-designer-env

3. Activate the environment in the shell. This will use `python` and other binaries like `pip` that are located your pyenv. You should do this whenever running any python/django scripts.

        $ source ~/pyenvs/genome-designer-env/bin/activate .

4. Install the dependencies in your virtual environment. We've exported the requirements in the requirements.txt file. In theory, these should all be installable with the single command:

        $ pip install -r requirements.txt

However, in reality, this doesn't seem to work perfectly. In particular, it may
be necessary to install specific packages first.

NOTE: Watch changes to requirements.txt and re-run the install command when
collaborators add new dependencies.

### Async Queue - Celery and RabbitMQ

Asynchronous processing is necessary for many of the analysis tasks in this
application.  We use the open source project celery since it is being actively
developed and has a library for integrating with Django. Celery requires a
message broker, for which we use RabbitMQ which is the default for Celery.

1. Install Celery

    The `celery` and `django-celery` packages are listed in
    requirements.txt and should be installed in your virtualenv following the
    instructions above.

2. Install RabbitMQ - On Ubuntu, install using sudo:

        $ sudo apt-get install rabbitmq-server

    Full instructions are [here](http://www.rabbitmq.com/download.html).


## Running the application

0. Activate your virtualenv, e.g.:

        $ source ~/pyenvs/genome-designer-env/bin/activate .

1. Navigate to the the `genome_designer/` dir.

2. From one terminal, start the celery server.

        (venv)$ ./run_celery.sh

3. Open another terminal and start the django server.

        (venv)$ python manage.py runserver

4. Visit the url <http://localhost:8000/> to see the demo.


## Tests

We'll be adding more tests as we go and updating the following instructions.
The following command runs all the tests related to Django:

    (venv)$ python manage.py test

NOTE: There are several django-registration tests failing at the moment.

In order to run only the tests related to our project, run:

    (venv)$ python manage.py test main

### Adding Tests

The way the current strategy of Django's default test runner works, is that it
checks the `tests.py` file in every django app (we only have main right now),
and runs the tests there.  For now, we've defined a test suite in `tests.py`
that discovers any files under the `main` directory of the name form `test*.py`
(e.g. `test_import_util.py`), so the current strategy for adding a new test
module is to create a module of this form under the `main` directory.


## Bootstrapping Test Data

From the `genome_designer` directory, run:

    (venv)$ python scripts/bootstrap_data.py

NOTE: This will delete the entire dev database and re-create it with the
hard-coded test models only.
