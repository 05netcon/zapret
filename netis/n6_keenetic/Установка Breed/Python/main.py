import os
import sys
import shutil
import time
import socket
import paramiko
import gateway
import filecmp
from scp import SCPClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Data')

def get_hex_offset(filename, offset, length):
    with open(filename, 'rb') as f:
        f.seek(offset)
        return f.read(length).hex()

def modify_file(file_path):
    with open(file_path, "rb") as f:
        data = bytearray(f.read())

    if data[0x190:0x195] == bytearray.fromhex("D2 52 06 AA 28"):
        data[0x190] = 0x92
    copied_data = data[0x7EF20:0x7EF20+6]
    data[0xA:0xA+6] = copied_data
    with open(file_path, "wb") as f:
        f.write(data)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    grandparent_dir = os.path.dirname(parent_dir)
    target_dir = os.path.join(grandparent_dir, 'Keenetic')

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    new_file_path = os.path.join(target_dir, os.path.basename(file_path))
    shutil.move(file_path, new_file_path)
    return new_file_path

def get_mtd_partitions(client):
    stdin, stdout, stderr = client.exec_command('cat /proc/mtd')
    data = stdout.read().decode()
    lines = data.split('\n')
    partitions = {}
    for line in lines:
        if 'Factory' in line:
            partitions['Factory'] = line.split(':')[0]
        elif 'factory' in line:
            partitions['Factory'] = line.split(':')[0]   
    return partitions

def install_package(client, package_name, package_file):
    print(f"Устанавливаем пакет {package_name}...")

    if not os.path.exists(package_file):
        raise Exception(f"Файл {package_file} не найден в папке Data. Проверьте наличие файла.")

    scp = SCPClient(client.get_transport())
    scp.put(package_file, f'/tmp/{package_name}.ipk')
    stdin, stdout, stderr = client.exec_command(f'opkg install --force-depends /tmp/{package_name}.ipk', get_pty=True)
    time.sleep(2)
    install_output = stdout.read() + stderr.read()
    stdin, stdout, stderr = client.exec_command(f'opkg list-installed | grep {package_name}')
    check_output = stdout.read() + stderr.read()
    if package_name.encode() not in check_output:
        raise Exception(f"")
    else:
        print(f"Пакет {package_name} успешно установлен.")

def backup(client, partitions):
    commands = [
        (f'dd if=/dev/{partitions["Factory"]} of=/tmp/OpenWrt.mtd2.bin count=1024', 'OpenWrt.mtd2.bin')
    ]
    for command, filename in commands:
        stdin, stdout, stderr = client.exec_command(command, get_pty=True)
        time.sleep(2)
        scp = SCPClient(client.get_transport())
        scp.get(f'/tmp/{filename}', os.path.join(DATA_DIR, filename))
        if filename == 'OpenWrt.mtd2.bin':
            new_filename = 'EEPROM_'+ get_hex_offset(os.path.join(DATA_DIR, filename), 0x7EF20, 6).upper() + '.bin'
            shutil.copy(os.path.join(DATA_DIR, filename), os.path.join(DATA_DIR, new_filename))
            if filecmp.cmp(os.path.join(DATA_DIR, filename), os.path.join(DATA_DIR, new_filename)):
                os.remove(os.path.join(DATA_DIR, filename))
            filename = new_filename
            eeprom_file_path = os.path.join(DATA_DIR, filename)
            print(f"Модифицируем файл {filename}...")
            print(f"")
            new_eeprom_path = modify_file(eeprom_file_path)

            if os.path.exists(new_eeprom_path):
                print(f"Модифицированный файл {os.path.basename(new_eeprom_path)} перемещён в папку Keenetic и готов к использованию.")
            else:
                print(f"Ошибка: файл {os.path.basename(new_eeprom_path)} не найден в папке Keenetic!")

def write_loader(client):
    stdin, stdout, stderr = client.exec_command('insmod mtd-rw i_want_a_brick=1')
    scp = SCPClient(client.get_transport())
    scp.put(os.path.join(DATA_DIR, 'breed_netis_n6.bin'), '/tmp/breed_netis_n6.bin')
    stdin, stdout, stderr = client.exec_command('mtd write /tmp/breed_netis_n6.bin u-boot', get_pty=True)
    time.sleep(2)
    data = stdout.read() + stderr.read()


def main():
    print ("+--------------------------------------------------------------+")
    print ("|          Breed installer for Netis N6 by spatiumstas         |")
    print ("+--------------------------------------------------------------+")

    router_ip = gateway.get_ip_address()
    username = 'root'
    password = 'root'
    ssh_port = '22'

    try:
        with paramiko.SSHClient() as client:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=router_ip, username=username, password=password, port=ssh_port)
            time.sleep(2)
            stdin, stdout, stderr = client.exec_command('opkg list-installed | grep kmod-mtd-rw')
            check_output = stdout.read() + stderr.read()

            if b'kmod-mtd-rw' not in check_output:
                print("Пакет kmod-mtd-rw не установлен.")
                try:
                    kmod_file = os.path.join(DATA_DIR, 'kmod-mtd-rw.ipk')
                    install_package(client, 'kmod-mtd-rw', kmod_file)
                    package_installed = True
                except Exception as e:
                    package_installed = False

                    try:
                        kmod_file2 = os.path.join(DATA_DIR, 'kmod-mtd-rw2.ipk')
                        install_package(client, 'kmod-mtd-rw2', kmod_file2)
                        package_installed = True
                    except Exception as e2:
                        print(f"")
                        package_installed = False
            else:
                print("Пакет kmod-mtd-rw уже установлен.")

            partitions = get_mtd_partitions(client)
            backup(client, partitions)
            write_loader(client)
            print ("")
            print("+--------------------------------------------------------------+")
            print("|              Загрузчик Breed успешно установлен              |")
            print("+--------------------------------------------------------------+")
            print ("")
            print ('Перезагрузка в Breed...')
            print (f"")
            client.exec_command('reboot')
            time.sleep(2)
            client.close()
            sys.exit()
    except Exception as e:
        print(f"Произошла ошибка: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
