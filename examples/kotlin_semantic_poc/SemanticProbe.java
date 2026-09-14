import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.jetbrains.kotlin.cli.jvm.K2JVMCompiler;
import org.jetbrains.kotlin.cli.common.ExitCode;
import org.jetbrains.org.objectweb.asm.*;

/** Compiler-resolved JVM calls, not a source-level Analysis API implementation. */
public final class SemanticProbe {
    static String q(String text) {
        return "\"" + text.replace("\\", "\\\\").replace("\"", "\\\"")
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "\"";
    }
    public static void main(String[] args) throws Exception {
        long start = System.nanoTime();
        String root=args[0], out=args[1];
        Files.createDirectories(Paths.get(out));
        Path sourceCalls=Paths.get(out,"source-calls.json");
        Files.deleteIfExists(sourceCalls);
        System.setProperty("arcade.source.calls",sourceCalls.toString());
        ByteArrayOutputStream diagnostics=new ByteArrayOutputStream();
        ExitCode code=new K2JVMCompiler().exec(new PrintStream(diagnostics),
            "-no-stdlib", "-no-reflect", "-no-jdk", "-jvm-target", "17",
            "-classpath", root+"/lib/kotlin-stdlib-2.0.21.jar",
            "-Xplugin="+root+"/build/source-plugin.jar", "-d", out, root+"/MainActivity.kt", root+"/Overloads.kt");
        long compiled=System.nanoTime();
        List<String> calls=new ArrayList<>();
        if(code==ExitCode.OK) {
            try(var files=Files.walk(Paths.get(out))) {
                for(Path file:files.filter(p->p.toString().endsWith(".class")).sorted().toList()) {
                    new ClassReader(Files.readAllBytes(file)).accept(new ClassVisitor(Opcodes.ASM9) {
                        String owner, source;
                        public void visit(int v,int a,String n,String s,String parent,String[] interfaces){owner=n;}
                        public void visitSource(String s,String debug){source=s;}
                        public MethodVisitor visitMethod(int access,String name,String desc,String signature,String[] exceptions) {
                            return new MethodVisitor(Opcodes.ASM9) {
                                int line=-1;
                                public void visitLineNumber(int n,Label label){line=n;}
                                public void visitMethodInsn(int opcode,String target,String method,String descriptor,boolean itf) {
                                    calls.add("{\"caller\":"+q(owner.replace('/','.')+"."+name)
                                        +",\"callerSignature\":"+q(desc)+",\"callee\":"+q(target.replace('/','.')+"."+method)
                                        +",\"calleeSignature\":"+q(descriptor)+",\"file\":"+q(source)
                                        +",\"line\":"+line+",\"provenance\":\"kotlin-2.0.21-bytecode\"}");
                                }
                            };
                        }
                    },0);
                }
            }
        }
        ByteArrayOutputStream invalidDiagnostics=new ByteArrayOutputStream();
        ExitCode invalidCode=new K2JVMCompiler().exec(new PrintStream(invalidDiagnostics),
            "-no-stdlib", "-no-reflect", "-no-jdk", "-jvm-target", "17",
            "-classpath", root+"/lib/kotlin-stdlib-2.0.21.jar",
            "-d",out+"/invalid",root+"/Invalid.kt");
        String resolved=code==ExitCode.OK ? Files.readString(sourceCalls) : "[]";
        System.out.println("SEMANTIC_RESULT {\"compileExit\":"+q(code.toString())
            +",\"diagnostics\":"+q(diagnostics.toString("UTF-8"))
            +",\"compileMs\":"+(compiled-start)/1e6+",\"totalMs\":"+(System.nanoTime()-start)/1e6
            +",\"invalidCompileExit\":"+q(invalidCode.toString())+",\"invalidDiagnostics\":"+q(invalidDiagnostics.toString("UTF-8"))
            +",\"sourceCalls\":"+resolved+",\"calls\":["+String.join(",",calls)+"]}");
        if(code!=ExitCode.OK) throw new IllegalStateException("Kotlin compilation failed: "+code);
    }
}
