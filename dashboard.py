import eventlet
eventlet.monkey_patch()

from dashboard_socketio import app, socketio

application = app
