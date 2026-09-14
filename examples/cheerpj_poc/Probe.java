import java.nio.file.Files;
import java.nio.file.Paths;

/** Runtime compatibility smoke, not a Kotlin semantic resolver. */
public final class Probe {
    public static int answer() { return 42; }
    public static void main(String[] args) throws Exception {
        Object value = Probe.class.getMethod("answer").invoke(null);
        String source = new String(Files.readAllBytes(Paths.get(args[0])), "UTF-8");
        if (!value.equals(42) || !source.contains("fun render(): Int = step01()")) {
            throw new AssertionError("Reflection or fixture read failed");
        }
        System.out.println("POC_OK java=" + System.getProperty("java.version")
            + " reflection=" + value + " source_chars=" + source.length());
    }
}
