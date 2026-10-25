import os
import sys
import winreg

def get_launch_command() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    exe_path = os.path.join(current_dir, "dist", "TurboCopy", "TurboCopy.exe")
    if os.path.exists(exe_path):
        return f'"{exe_path}" "%1"'
    
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = "pythonw.exe"
    app_py = os.path.join(current_dir, "app.py")
    return f'"{pythonw}" "{app_py}" "%1"'


def is_context_menu_installed() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\TurboCopy"):
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def install_context_menu() -> bool:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(current_dir, "icon.ico")
        cmd_str = get_launch_command()

        # 1. For all files (*)
        key_path = r"Software\Classes\*\shell\TurboCopy"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.SetValue(k, "", winreg.REG_SZ, "TurboCopy ile Kopyala...")
            if os.path.exists(icon_path):
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon_path)
            with winreg.CreateKey(k, "command") as k_cmd:
                winreg.SetValue(k_cmd, "", winreg.REG_SZ, cmd_str)

        # 2. For directories (Directory)
        key_dir_path = r"Software\Classes\Directory\shell\TurboCopy"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_dir_path) as k:
            winreg.SetValue(k, "", winreg.REG_SZ, "TurboCopy ile Kopyala...")
            if os.path.exists(icon_path):
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon_path)
            with winreg.CreateKey(k, "command") as k_cmd:
                winreg.SetValue(k_cmd, "", winreg.REG_SZ, cmd_str)

        return True
    except Exception as e:
        print("Error installing context menu:", e)
        return False


def uninstall_context_menu() -> bool:
    try:
        def delete_key_tree(root, subkey):
            try:
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_ALL_ACCESS) as k:
                    while True:
                        try:
                            child = winreg.EnumKey(k, 0)
                            delete_key_tree(root, f"{subkey}\\{child}")
                        except OSError:
                            break
                winreg.DeleteKey(root, subkey)
            except FileNotFoundError:
                pass

        delete_key_tree(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\TurboCopy")
        delete_key_tree(winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\TurboCopy")
        return True
    except Exception as e:
        print("Error uninstalling context menu:", e)
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        uninstall_context_menu()
        print("Sağ tık menüsünden kaldırıldı.")
    else:
        install_context_menu()
        print("Sağ tık menüsüne başarıyla eklendi.")
