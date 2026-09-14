package cases
class Overloads {
    fun choose(value: Int): Int = value
    fun choose(value: Long): Long = value
    fun useInt(): Int = choose(1)
    fun useLong(): Long = choose(1L)
}
