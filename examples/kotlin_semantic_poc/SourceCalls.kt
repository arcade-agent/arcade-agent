package arcade.poc

import java.io.File
import org.jetbrains.kotlin.backend.common.extensions.IrGenerationExtension
import org.jetbrains.kotlin.backend.common.extensions.IrPluginContext
import org.jetbrains.kotlin.compiler.plugin.CompilerPluginRegistrar
import org.jetbrains.kotlin.compiler.plugin.ExperimentalCompilerApi
import org.jetbrains.kotlin.config.CompilerConfiguration
import org.jetbrains.kotlin.ir.IrElement
import org.jetbrains.kotlin.ir.declarations.*
import org.jetbrains.kotlin.ir.expressions.IrCall
import org.jetbrains.kotlin.ir.util.fqNameWhenAvailable
import org.jetbrains.kotlin.ir.util.render
import org.jetbrains.kotlin.ir.visitors.IrElementVisitorVoid
import org.jetbrains.kotlin.ir.visitors.acceptChildrenVoid

@OptIn(ExperimentalCompilerApi::class)
class SourceCallsRegistrar : CompilerPluginRegistrar() {
    override val supportsK2 = true
    override fun ExtensionStorage.registerExtensions(configuration: CompilerConfiguration) {
        IrGenerationExtension.registerExtension(SourceCalls())
    }
}
@OptIn(org.jetbrains.kotlin.ir.symbols.UnsafeDuringIrConstructionAPI::class)
class SourceCalls : IrGenerationExtension {
    override fun generate(moduleFragment: IrModuleFragment, pluginContext: IrPluginContext) {
        val rows = mutableListOf<String>()
        fun quote(s: String) = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
        fun signature(fn: IrFunction) = fn.valueParameters.joinToString(",", "(", ")") { it.type.render() } + ":" + fn.returnType.render()
        for (file in moduleFragment.files) {
            var caller: IrFunction? = null
            file.acceptChildrenVoid(object : IrElementVisitorVoid {
                override fun visitElement(element: IrElement) { element.acceptChildrenVoid(this) }
                override fun visitFunction(declaration: IrFunction) {
                    val previous = caller
                    caller = declaration
                    declaration.acceptChildrenVoid(this)
                    caller = previous
                }
                override fun visitCall(expression: IrCall) {
                    val from = caller
                    val to = expression.symbol.owner
                    if (from != null) {
                        rows += "{\"caller\":" + quote(from.fqNameWhenAvailable?.asString() ?: from.name.asString()) +
                            ",\"callerSignature\":" + quote(signature(from)) +
                            ",\"callee\":" + quote(to.fqNameWhenAvailable?.asString() ?: to.name.asString()) +
                            ",\"calleeSignature\":" + quote(signature(to)) +
                            ",\"file\":" + quote(File(file.fileEntry.name).name) +
                            ",\"line\":" + (file.fileEntry.getLineNumber(expression.startOffset) + 1) +
                            ",\"provenance\":\"kotlin-2.0.21-resolved-ir\"}"
                    }
                    expression.acceptChildrenVoid(this)
                }
            })
        }
        File(System.getProperty("arcade.source.calls")).writeText("[" + rows.joinToString(",") + "]")
    }
}
