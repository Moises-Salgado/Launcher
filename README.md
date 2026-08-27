# Centro de Comando Clínico

Aplicación de escritorio para apoyar flujos locales de radioterapia. Integra
extracción de órdenes de trabajo, revisión de estructuras y procesamiento
DICOM en una interfaz nativa PySide6. No utiliza navegador ni requiere iniciar
un servidor.

> **Importante:** el instalador disponible es un paquete `.deb` para equipos
> `amd64` basados en Debian/Ubuntu. Un archivo `.deb` no es un instalador
> universal para todas las distribuciones Linux.

## Programas incluidos

- **P1 · Extraer datos desde PDF:** OTs UNIQUE, Halcyon 1/2, Control de
  Calidad y otros formularios ECM.
- **P2 · Visor TXT de estructuras:** agrupa órganos, lateralidades y campos de
  dosis.
- **P3 · Editor de nombres y visualizador DICOM:** separa modalidades y series,
  permite recorrer imágenes y exporta copias modificando únicamente
  `PatientName`.
- **P4 · Compatibilizar CD para Eclipse:** prepara estudios de HGGB y de otros
  centros, como Clínica Los Andes o Clínica Biobío.

Todo el procesamiento se realiza localmente en el computador.

## Compatibilidad del instalador

El paquete actual es:

```text
centro-comando-clinico_1.4.1_amd64.deb
```

Requisitos:

- distribución basada en Debian con `apt` y `dpkg`;
- arquitectura Intel/AMD de 64 bits (`amd64` o `x86_64`);
- glibc 2.38 o superior;
- Python 3.12 disponible en los repositorios de la distribución;
- escritorio gráfico compatible con X11 o Wayland;
- aproximadamente 2 GB de espacio libre para el programa y sus dependencias.

La compilación se prueba en **Ubuntu 24.04 LTS `amd64`**. También puede
instalarse en distribuciones derivadas que proporcionen todas las dependencias
anteriores. No es compatible con Ubuntu 22.04, Debian 12, equipos ARM64 ni
distribuciones basadas únicamente en RPM o Pacman.

Para comprobar la arquitectura antes de instalar:

```bash
dpkg --print-architecture
```

El resultado debe ser:

```text
amd64
```

## Descargar el paquete ZIP desde MediaFire

Descargue este archivo y extráigalo antes de instalar:

```text
Centro_Comando_Clinico_1.4.1_Linux_amd64.zip
```

Al extraerlo se crea la carpeta `Centro_Comando_Clinico_1.4.1_Linux`, que
contiene:

```text
centro-comando-clinico_1.4.1_amd64.deb
README.md
SHA256SUMS
```

También puede publicarse el ZIP como archivo adjunto de una **GitHub Release**.
El instalador es demasiado grande para guardarlo normalmente dentro del
historial Git.

Para extraerlo desde la terminal en Ubuntu configurado en español:

```bash
cd "$HOME/Descargas"
unzip Centro_Comando_Clinico_1.4.1_Linux_amd64.zip
cd Centro_Comando_Clinico_1.4.1_Linux
```

En un sistema configurado en inglés, reemplace `Descargas` por `Downloads`.
También puede hacer clic derecho sobre el ZIP y seleccionar **Extraer aquí**.

## Verificar la descarga

Abra una terminal dentro de la carpeta extraída. Si siguió las instrucciones
anteriores, ejecute:

```bash
sha256sum -c SHA256SUMS
```

La comprobación correcta debe terminar con:

```text
centro-comando-clinico_1.4.1_amd64.deb: OK
```

Si aparece `FAILED`, no instale ese archivo: vuelva a descargar el ZIP desde
el enlace oficial de MediaFire o desde la GitHub Release.

## Instalar desde la terminal

Desde la carpeta que contiene el instalador:

```bash
sudo apt update
sudo apt install ./centro-comando-clinico_1.4.1_amd64.deb
```

El prefijo `./` es necesario para indicarle a APT que se trata de un archivo
local. APT instalará también las dependencias disponibles en los repositorios
del sistema.

No es necesario crear un entorno virtual, ejecutar `pip` ni instalar
dependencias de Python manualmente.

La instalación debe realizarse con esos comandos. En algunos escritorios Linux,
el doble clic sobre un `.deb` abre su contenido como si fuera una carpeta, pero
no instala el programa.

## Ejecutar el programa

Después de instalarlo, abra el menú de aplicaciones y busque:

```text
Centro de Comando Clínico
```

También puede iniciarlo desde cualquier terminal, sin `sudo`:

```bash
centro-comando-clinico
```

No ejecute la aplicación como administrador. Los archivos exportados deben
pertenecer a la cuenta de la persona que utiliza el programa.

## Comprobar la instalación

Para consultar el estado y la versión instalada:

```bash
dpkg-query -W -f='Estado: ${db:Status-Abbrev}\nVersión: ${Version}\n' centro-comando-clinico
```

Una instalación correcta mostrará un estado que comienza con `ii` y la versión
`1.4.1`.

Para comprobar el comando ejecutable:

```bash
command -v centro-comando-clinico
```

El resultado esperado es:

```text
/usr/bin/centro-comando-clinico
```

## Actualizar a una versión nueva

Descargue el nuevo `.deb`, verifique su `SHA256SUMS` y ejecute:

```bash
sudo apt install ./centro-comando-clinico_VERSION_NUEVA_amd64.deb
```

APT reemplazará la versión anterior. La configuración personal almacenada en
la carpeta del usuario se conserva.

## Desinstalar

Para quitar el programa y conservar la configuración personal:

```bash
sudo apt remove centro-comando-clinico
```

Para eliminar además la configuración administrada por el paquete:

```bash
sudo apt purge centro-comando-clinico
```

La configuración creada dentro de la carpeta personal no se elimina
automáticamente. Si desea borrarla manualmente, después de desinstalar puede
eliminar:

```text
~/.config/centro-comando-clinico/
```

Revise esa ruta antes de borrarla. Las exportaciones clínicas no se eliminan
al desinstalar.

## Ubicaciones utilizadas

| Elemento | Ubicación |
|---|---|
| Programa instalado | `/opt/centro-comando-clinico/` |
| Comando de inicio | `/usr/bin/centro-comando-clinico` |
| Acceso del menú | `/usr/share/applications/centro-comando-clinico.desktop` |
| Configuración personal | `~/.config/centro-comando-clinico/config.json` |
| Salida predeterminada de OTs | `~/Escritorio/OTs/` |
| Salida predeterminada DICOM | `~/Escritorio/DICOM_Export/` |

Los directorios de salida pueden cambiarse desde la aplicación.

## Solución de problemas

### APT informa dependencias no satisfechas

Actualice el índice de paquetes y repita la instalación:

```bash
sudo apt update
sudo apt install ./centro-comando-clinico_1.4.1_amd64.deb
```

No utilice `dpkg --force-*`. Si APT indica que faltan Python 3.12 o glibc 2.38,
la distribución no es compatible con esta compilación.

### El programa no aparece en el menú

Cierre la sesión y vuelva a iniciarla, o ejecútelo directamente:

```bash
centro-comando-clinico
```

### El programa no abre

Ejecútelo desde una terminal para ver el mensaje de error:

```bash
centro-comando-clinico
```

Compruebe después la integridad de las dependencias:

```bash
sudo apt --fix-broken install
```

Al reportar un problema, adjunte la versión de Ubuntu, el resultado de
`dpkg --print-architecture` y el mensaje de la terminal. No adjunte archivos
DICOM, nombres de pacientes ni otros datos clínicos identificables.

## Privacidad y seguridad clínica

- El programa procesa PDF y DICOM localmente y no los envía a servicios web.
- Conserve siempre una copia original de los CD, DICOM y órdenes de trabajo.
- Revise el resultado exportado antes de importarlo en otro sistema clínico.
- P3 advierte sobre el separador `^` de `PatientName`; no lo elimine sin
  comprender la estructura del nombre DICOM.
- La aplicación es una herramienta de apoyo operativo y no reemplaza la
  validación clínica ni el software diagnóstico certificado.

## Generar el `.deb` desde el código fuente

En Ubuntu 24.04 instale las herramientas de construcción:

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip fakeroot dpkg-dev desktop-file-utils
```

Desde la raíz del repositorio:

```bash
chmod +x packaging/build_deb.sh packaging/validate_deb.sh
./packaging/build_deb.sh 1.4.1
./packaging/validate_deb.sh dist/centro-comando-clinico_1.4.1_amd64.deb
```

Los archivos publicables quedarán en:

```text
dist/centro-comando-clinico_1.4.1_amd64.deb
dist/SHA256SUMS
```

## Desarrollo

La interfaz principal está construida con PySide6. Las dependencias de Python
se encuentran fijadas en `requirements-lock.txt`; el empaquetado utiliza un
entorno privado dentro de `/opt/centro-comando-clinico/`.

Antes de publicar una versión ejecute siempre el validador incluido y pruebe el
instalador en una instalación limpia de Ubuntu 24.04 `amd64`.
