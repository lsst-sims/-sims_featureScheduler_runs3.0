from astropy.coordinates import EarthLocation
from astropy.utils.data import clear_download_cache
from astropy.time import Time

# Clear the download cache and re-download things needed.
clear_download_cache()  
t = Time('2016:001')
t.ut1  
EarthLocation.get_site_names()
