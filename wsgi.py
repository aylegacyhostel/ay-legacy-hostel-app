import app as app_module
from photo_storage import install_photo_storage

install_photo_storage(app_module)
app = app_module.app
