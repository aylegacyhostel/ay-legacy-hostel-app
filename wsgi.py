import app as app_module
from photo_storage import install_photo_storage
from backup_restore import install_backup_restore

install_photo_storage(app_module)
install_backup_restore(app_module)
app = app_module.app
