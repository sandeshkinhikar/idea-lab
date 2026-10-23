import java.io.InputStream;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;

public class GisImageDownloader {
    
    // The target directory your ML scripts are expecting
    private static final String DATA_DIR = "data/raw/";
    
    // The exact GIS GeoTIFF rasters required for the GeoCool project
    private static final String[] RASTER_FILES = {
        "nagpur_lst.tif",
        "nagpur_ndvi.tif",
        "nagpur_slope.tif",
        "nagpur_pop.tif"
    };

    public static void main(String[] args) {
        System.out.println("Initializing GIS Raster Download Pipeline...");
        
        // In a live production environment, this base URL points to an authenticated 
        // Google Earth Engine REST endpoint or a cloud bucket hosting the exported images.
        String cloudStorageUrl = "https://geocool-satellite-data.s3.amazonaws.com/nagpur/5km_radius/";

        try {
            Path dirPath = Paths.get(DATA_DIR);
            if (!Files.exists(dirPath)) {
                Files.createDirectories(dirPath);
                System.out.println("Created missing directory: " + DATA_DIR);
            }

            for (String fileName : RASTER_FILES) {
                System.out.println("Fetching -> " + fileName);
                URL url = new URL(cloudStorageUrl + fileName);
                
                try (InputStream in = url.openStream()) {
                    Path destination = Paths.get(DATA_DIR + fileName);
                    // This overwrites any partial or corrupted files from a previous run
                    Files.copy(in, destination, StandardCopyOption.REPLACE_EXISTING);
                    System.out.println("Successfully saved to: " + destination.toAbsolutePath());
                } catch (Exception e) {
                    System.err.println("Error downloading " + fileName + ": " + e.getMessage());
                }
            }
            
            System.out.println("All GIS image assets provisioned.");
            
        } catch (Exception e) {
            System.err.println("Critical pipeline failure: " + e.getMessage());
        }
    }
}
