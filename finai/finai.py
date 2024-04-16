"""
Main application
"""
import logging
import os
import json
import pathlib
import requests
import streamlit as st
from langchain_community.llms import Ollama
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import (
    CSVLoader,
    PyMuPDFLoader,
    TextLoader,
    UnstructuredPowerPointLoader,
    Docx2txtLoader,
    UnstructuredExcelLoader,
    BSHTMLLoader,
)
from langchain_experimental.text_splitter import SemanticChunker
from langchain_community.embeddings import HuggingFaceInstructEmbeddings

logger = logging.getLogger(__name__)

FILE_LOADERS = {
    "csv": CSVLoader,
    "docx": Docx2txtLoader,
    "pdf": PyMuPDFLoader,
    "pptx": UnstructuredPowerPointLoader,
    "txt": TextLoader,
    "xlsx": UnstructuredExcelLoader,
    "html": BSHTMLLoader,
}

ACCEPTED_FILE_TYPES = list(FILE_LOADERS)


# Message classes
class Message:
    """
    Base message class
    """
    def __init__(self, content):
        self.content = content


class HumanMessage(Message):
    """
    Represents a message from the user.
    """


class AIMessage(Message):
    """
    Represents a message from the AI.
    """

@st.cache_resource
def load_model():
    with st.spinner("Downloading Instructor XL Embeddings Model locally....please be patient"):
        embedding_model=HuggingFaceInstructEmbeddings(model_name="hkunlp/instructor-large", model_kwargs={"device": "cuda"})
    return embedding_model

class ChatWithFile:
    """
    Main class to handle the interface with the LLM
    """
    def __init__(self, file_path, file_type):
        """
        Perform initial parsing of the uploaded file and initialize the
        chat instance.

        :param file_path: Full path and name of uploaded file
        :param file_type: File extension determined after upload
        """
        self.embedding_model = load_model()
        self.vectordb = None
        loader = FILE_LOADERS[file_type](file_path=file_path)
        pages = loader.load_and_split()
        docs = self.split_into_chunks(pages)
        self.store_in_chroma(docs)

        self.memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True
        )

        self.llm = Ollama(model=st.session_state['selected_model'], base_url="http://ollama:11434")

        self.qa = ConversationalRetrievalChain.from_llm(
            self.llm,
            self.vectordb.as_retriever(search_kwargs={"k": 10}),
            memory=self.memory
        )

    def split_into_chunks(self, pages):
        """
        Split the document pages into chunks based on similarity

        :return: Result of langchain_experimental.text_splitter.SemanticChunker
        """
        text_splitter = SemanticChunker(
            embeddings=self.embedding_model,
            breakpoint_threshold_type="percentile"
        )
        return text_splitter.split_documents(pages)

    def simplify_metadata(self, doc):
        """
        If the provided doc contains a metadata dict, iterate over the
        metadata and ensure values are stored as strings.

        :param doc: Chunked document to process
        :return: Document with any metadata values cast to string
        """
        metadata = getattr(doc, "metadata", None)
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if isinstance(value, (list, dict)):
                    metadata[key] = str(value)
        return doc

    def reciprocal_rank_fusion(self, all_results):  # , k=60):
        """
        Process the scoring for each document generated by the LLM

        :param all_results: All score results generated by a query
        :param k: Currently unused
        :return: Sorted dict of all document scores
        """
        # Assuming each result in all_results can be uniquely identified for scoring
        # And assuming all_results is directly the list you want to work with
        fused_scores = {}
        for result in all_results:
            # Let's assume you have a way to uniquely identify each result; for simplicity, use its index
            doc_id = result["query"]  # or any unique identifier within each result
            if doc_id not in fused_scores:
                fused_scores[doc_id] = {"doc": result, "score": 0}
            # Example scoring adjustment; this part needs to be aligned with your actual scoring logic
            fused_scores[doc_id]["score"] += 1  # Simplified; replace with actual scoring logic

        reranked_results = sorted(fused_scores.values(), key=lambda x: x["score"], reverse=True)
        return reranked_results

    def create_synthesis_prompt(self, original_question, all_results):
        """
        Create a prompt based on the original question to gain a composite
        prompt across the highest scored documents.

        :param original_question: Original prompt sent to the LLM
        :param all_results: Sorted (by score) results of original prompt
        :return: Prompt for a composite score based on original_question
        """
        # Sort the results based on RRF score if not already sorted; highest scores first
        sorted_results = sorted(all_results, key=lambda x: x["score"], reverse=True)
        #st.write("Sorted Results", sorted_results)
        prompt = (
            f"Based on the user's original question: '{original_question}', "
            "here are the answers to the original and related questions, "
            "ordered by their relevance (with RRF scores). Please synthesize "
            "a comprehensive answer focusing on answering the original "
            "question using all the information provided below:\n\n"
        )

        # Include RRF scores in the prompt, and emphasize higher-ranked answers
        for idx, result in enumerate(sorted_results):
            prompt += f"Answer {idx + 1} (Score: {result['score']}): {result['answer']}\n\n"

        prompt += (
            "Given the above answers, especially considering those with "
            "higher scores, please provide the best possible composite answer "
            "to the user's original question."
        )

        return prompt

    def store_in_chroma(self, docs):
        """
        Store each document in Chroma

        :param docs: Result of splitting pages into chunks
        :return: None
        """
        # Simplify metadata for all documents
        docs = [self.simplify_metadata(doc) for doc in docs]

        # Proceed with storing documents in Chroma
        self.vectordb = Chroma.from_documents(docs, embedding=self.embedding_model)
        self.vectordb.persist()

    def extract_json_from_response(self, response_text):
        """
        If a response is received that should have JSON embedded in the
        output string, look for the opening and closing tags ([]) then extract
        the matching text.

        :param response_text: Response from LLM that might contain JSON
        :return: Python object returned by json.loads. If no JSON response
            was identified, an empty tuple.
        """
        json_result = ()
        try:
            json_start = response_text.find("[")
            json_end = response_text.rfind("]") + 1
            json_str = response_text[json_start:json_end]
            json_result = json.loads(json_str)
            #st.write("Parsed related queries:", related_queries)
        except (ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to parse JSON: %s", e)
            #st.error(f"Failed to parse JSON: {e}")
            # related_queries = []
        return json_result

    def generate_related_queries(self, original_query):
        """
        Create a list of related queries based on the initial question

        :param original_query: Initial question
        :return: Related queries generated by the LLM. If none, empty tuple.
        """
        # NOTE: This prompt is split on sentences for readability. No newlines
        # will be included in the output due to implied line continuation.
        prompt = (
            f"In light of the original inquiry: '{original_query}', let's "
            "delve deeper and broaden our exploration. Please construct a "
            "JSON array containing four distinct but interconnected search "
            "queries. Each query should reinterpret the original prompt's "
            "essence, introducing new dimensions or perspectives to "
            "investigate. Aim for a blend of complexity and specificity in "
            "your rephrasings, ensuring each query unveils different facets "
            "of the original question. This approach is intended to "
            "encapsulate a more comprehensive understanding and generate the "
            "most insightful answers possible. Only respond with the JSON "
            "array itself."
        )
        response = self.llm.invoke(input=prompt)

        if hasattr(response, "content"):
            # Directly access the 'content' if the response is the expected object
            generated_text = response.content
        elif isinstance(response, dict):
            # Extract 'content' if the response is a dict
            generated_text = response.get("content")
        else:
            # Fallback if the structure is different or unknown
            generated_text = str(response)
            #st.error("Unexpected response format.")

        #st.write("Response content:", generated_text)

        # Assuming the 'content' starts with "content='" and ends with "'"
        # Attempt to directly parse the JSON part, assuming no other wrapping
        related_queries = self.extract_json_from_response(generated_text)

        return related_queries
   
    def chat(self, question):
        """
        Main chat interface. Generate a list of queries to send to the LLM, then
        collect responses and append to the conversation_history instance
        attribute, for display after the chat completes.

        :param question: Initial question asked by the uploader
        :return: None
        """
        # Generate related queries based on the initial question
        related_queries_dicts = self.generate_related_queries(question)

        # Ensure that queries are in string format, extracting the 'query' value from dictionaries
        related_queries_list = [q["query"] for q in related_queries_dicts]

        # Combine the original question with the related queries
        queries = [question] + related_queries_list

        all_results = []

        for query_text in queries:
            # response = None
            response = self.qa.invoke(query_text)

            # Process the response
            if response:
                st.write("Query: ", query_text)
                st.write("Response: ", response["answer"])
                all_results.append(
                    {
                        "query": query_text,
                        "answer": response["answer"]
                    }
                )
            else:
                st.write("No response received for: ", query_text)

        # After gathering all results, let's ask the LLM to synthesize a comprehensive answer
        if all_results:
            # Assuming reciprocal_rank_fusion is correctly applied and scored_results is prepared
            reranked_results = self.reciprocal_rank_fusion(all_results)
            # Prepare scored_results, ensuring it has the correct structure
            scored_results = [{"score": res["score"], **res["doc"]} for res in reranked_results]
            synthesis_prompt = self.create_synthesis_prompt(question, scored_results)
            synthesized_response = self.llm.invoke(synthesis_prompt)

            if synthesized_response:
                # Assuming synthesized_response is an AIMessage object with a 'content' attribute
                st.write(synthesized_response)
                final_answer = synthesized_response
            else:
                final_answer = "Unable to synthesize a response."

            # Update conversation history with the original question and the synthesized answer
            #self.conversation_history.append(HumanMessage(content=question))
            #self.conversation_history.append(AIMessage(content=final_answer))

            return {"answer": final_answer}

        self.conversation_history.append(HumanMessage(content=question))
        self.conversation_history.append(AIMessage(content="No answer available."))
        return {"answer": "No results were available to synthesize a response."}


def get_ollama_models(base_url):
    try:       
        response = requests.get(f"{base_url}api/tags")  # Corrected endpoint
        response.raise_for_status()
        models_data = response.json()
        # Extract just the model names for the dropdown
        models = [model['name'] for model in models_data.get('models', [])]
        return models
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to get models from Ollama: {e}")
        return []
        
def upload_and_handle_file():
    """
    Present the file upload context. After upload, determine the file extension
    and save the file. Set session state for the file path and type of file
    for use in the chat interface.

    :return: None
    """
    st.title("Document Buddy - Chat with Document Data")
    uploaded_file = st.file_uploader(
        label=(
            f"Choose a {', '.join(ACCEPTED_FILE_TYPES[:-1]).upper()}, or "
            f"{ACCEPTED_FILE_TYPES[-1].upper()} file"
        ),
        type=ACCEPTED_FILE_TYPES
    )
    if uploaded_file:
        # Determine the file type and set accordingly
        file_type = pathlib.Path(uploaded_file.name).suffix
        file_type = file_type.replace(".", "")

        if file_type:  # Will be an empty string if no extension
            csv_pdf_txt_path = os.path.join("temp", uploaded_file.name)
            if not os.path.exists("temp"):
                os.makedirs("temp")
            with open(csv_pdf_txt_path, "wb") as f:
                f.write(uploaded_file.getvalue())
            st.session_state["file_path"] = csv_pdf_txt_path
            st.session_state["file_type"] = file_type  # Store the file type in session state
            st.success(f"{file_type.upper()} file uploaded successfully.")
            # Fetch and display the models in a select box
            models = get_ollama_models("http://ollama:11434/")  # Make sure to use the correct base URL
            if models:
                selected_model = st.selectbox("Select Model", models)
                st.session_state['selected_model'] = selected_model            
                st.button(
                    "Proceed to Chat",
                    on_click=lambda: st.session_state.update({"page": 2})
                )
        else:
            st.error(
                f"Unsupported file type. Please upload a "
                f"{', '.join(ACCEPTED_FILE_TYPES[:-1]).upper()}, or "
                f"{ACCEPTED_FILE_TYPES[-1].upper()} file."
            )

def transform_input(user_input):
    if user_input.startswith("earning"):
        company_name = user_input.split("earning call ")[1]
        transformed_input = [
            f"Summarizing the key points from the earnings call transcript of {company_name}?",
            f"Please Extract financial metrics from the {company_name} call into a table, list, etc.",
            f"Quickly find out the risks, challenges, and opportunities mentioned in the calli of {company_name}?",
            f"Analyzing the overall sentiment of the earnings call of {company_name}?",
            f"What are the main questions asked by analysts in the earnings call transcript of {company_name}?"
        ]
        return transformed_input
    else:
        return user_input


def chat_interface():
    """
    Main chat interface - invoked after a file has been uploaded.

    :return: None
    """
    st.title("Document Buddy - Chat with Document Data")
    file_path = st.session_state.get("file_path")
    file_type = st.session_state.get("file_type")
    if not file_path or not os.path.exists(file_path):
        st.error("File missing. Please go back and upload a file.")
        return

    if "chat_instance" not in st.session_state:
        st.session_state["chat_instance"] = ChatWithFile(
            file_path=file_path,
            file_type=file_type
        )

    user_input = st.text_input("Ask a question about the document data: Enter earning call [company name] or 10k [company name] for automatic prompts:")
    if user_input and st.button("Send"):
        with st.spinner("Thinking..."):
            user_input = transform_input(user_input)
            if isinstance(user_input,list):
                for query in user_input:
                    st.write(query)
                    top_result = st.session_state["chat_instance"].chat(query)
            else:
                st.write(user_input)
                top_result = st.session_state["chat_instance"].chat(user_input)

            # Display the top result's answer as markdown for better readability
            #if top_result:
                #st.markdown("**Top Answer:**")
            #    st.markdown(f"> {top_result['answer']}")
            #else:
            #    st.write("No top result available.")

            # Display chat history
            #st.markdown("**Chat History:**")
            #for message in st.session_state["chat_instance"].conversation_history:
            #    prefix = "*You:* " if isinstance(message, HumanMessage) else "*AI:* "
            #    st.markdown(f"{prefix}{message.content}")


if __name__ == "__main__":
    if "page" not in st.session_state:
        st.session_state["page"] = 1

    if st.session_state["page"] == 1:
        upload_and_handle_file()
    elif st.session_state["page"] == 2:
        chat_interface()
